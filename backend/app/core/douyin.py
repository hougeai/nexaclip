"""抖音渠道：f2（a_bogus 签名）探测与下载，对外接口与 ytdlp 渠道同构。

【对外入口】上层只碰这三个（controllers/媒体路由按域名分流到此）：
    is_douyin_url(url)   是否抖音链接
    probe(url)           探测元信息 → 与 ytdlp.probe 同构的 dict
    download(url, ...)   下载成品 → {path, filename, size, ext}
其余都是它俩的内部 helper，按「探测链 → 下载链 → 工具」分节，不必从头读。

为什么独立渠道：抖音 web API 要求 a_bogus 签名，且「请求指纹」必须与 cookies 的
真实浏览器一致，yt-dlp 追不上其风控迭代（官方多年 site-bug）。f2 开源满血 ab 算法；
conf/conf.yaml 的浏览器指纹必须与 cookies 导出浏览器版本对齐，否则 403。

cookies 来源：浏览器插件导出的 Netscape txt，路径见 DOUYIN_COOKIES（默认 data/cookies/www.douyin.com_cookies.txt）。
其它站点的 cookie 绝不发给抖音。
"""

import asyncio
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, unquote_plus, urlparse

import httpx

from .config import settings
from .log import logger
from .storage import WORK_DIR
from .ytdlp import ProbeError

# f2 的加载有顺序约束，无法并入顶部 import 块：
#   1) 先固定 CWD 到 app 目录——f2 按相对路径 conf/conf.yaml 读浏览器指纹；
#   2) 再静音 f2 的日志——f2 用标准库 logging（不是 loguru），其 log_setup() 在模块级
#      执行 Path("./logs").mkdir() + FileHandler。提前注入 NullHandler 让
#      logger.hasHandlers() 短路，阻止目录创建和文件 handler（错误统一经 ProbeError 走本项目日志）；
#   3) 最后才能 import f2 并取 UA（UA 必须与 conf.yaml 指纹、cookies 三者一致）。
_APP_DIR = Path(__file__).resolve().parent.parent
if Path.cwd() != _APP_DIR:
    os.chdir(_APP_DIR)

import logging as _logging  # noqa: E402

_logging.getLogger('f2').addHandler(_logging.NullHandler())

from f2.apps.douyin.handler import DouyinHandler  # noqa: E402
from f2.apps.douyin.utils import ClientConfManager  # noqa: E402

_UA = ClientConfManager.user_agent()

# ---- 抖音 Argus SecSDK 签名 ----
# 2026-09 起 /aweme/v1/web/aweme/detail/ 位于 Argus 网关后，强制校验：
#   缺 uifid → 403 "Uifid Not Found"；有 uifid 无签名 → 403 "Signature Not Found"。
# 签名是纯 MD5，可本地算（实测只签 URL 即可，不必额外加请求头）：
#   md5(f"{uifid}_{秒级ts}_{SALT}_{规范化query}&uifid=&timestamp=")
# 规范化复刻 JS URLSearchParams：先 decode，再 quote(safe='*-._')。
_WEBSIGN_SALT = 'A96D855A08C0A9707F8BEF0D9A527E4E'


def _uifid_from_cookie() -> str:
    """从已加载的 cookie 头里取 UIFID（SecSDK 签名因子）。"""
    for part in _douyin_cookie_header().split(';'):
        k, _, v = part.partition('=')
        if k.strip().upper() == 'UIFID' and v.strip():
            return v.strip()
    return ''


def _sign_secsdk(url: str, uifid: str) -> str:
    """给 detail 接口 URL 追加 x-secsdk-web-signature；无 uifid 时原样返回。"""
    if not uifid:
        return url
    ts = int(time.time())
    base, _, query = url.partition('?')
    pairs = []
    for part in query.split('&'):
        if not part:
            continue
        k, _, v = part.partition('=')
        pairs.append((unquote_plus(k), unquote_plus(v)))
    canon = '&'.join(f'{quote(k, safe="*-._")}={quote(v, safe="*-._")}' for k, v in pairs)
    if 'uifid' not in [p.split('=', 1)[0] for p in canon.split('&') if p]:
        encrypted = quote(uifid, safe='*-._')
        canon = f'{canon}&uifid={encrypted}' if canon else f'uifid={encrypted}'
    signed_query = f'{canon}&timestamp={ts}'
    sig = hashlib.md5(f'{uifid}_{ts}_{_WEBSIGN_SALT}_{signed_query}'.encode('utf-8')).hexdigest()
    return f'{base}?{signed_query}&x-secsdk-web-signature={sig}'


from f2.apps.douyin.api import DouyinAPIEndpoints as _dyendpoint  # noqa: E402
from f2.apps.douyin.crawler import DouyinCrawler as _DouyinCrawler  # noqa: E402
import f2.apps.douyin.handler as _f2_douyin_handler  # noqa: E402


class _SecSdkDouyinCrawler(_DouyinCrawler):
    """只改写 detail 请求 URL（补签名），其它接口沿用 f2 原逻辑。"""

    async def fetch_post_detail(self, params):
        endpoint = self.bogus_manager.model_2_endpoint(
            self.headers.get('User-Agent'), _dyendpoint.POST_DETAIL, params.model_dump()
        )
        return await self._fetch_get_json(_sign_secsdk(endpoint, _uifid_from_cookie()))


# DouyinHandler.fetch_one_video 内部直接实例化 DouyinCrawler，故替换模块内引用。
# ⚠️ 升级 f2 后若 handler 不再引用该模块级名字，此补丁会失效（回到 403）。
_f2_douyin_handler.DouyinCrawler = _SecSdkDouyinCrawler

_AWEME_ID_RE = re.compile(r'/(?:video|note)/(\d+)')
_SEC_UID_RE = re.compile(r'/user/([A-Za-z0-9_\-]+)')
_GEAR_H_RE = re.compile(r'_(\d{3,4})_')

# cookies 文件（浏览器插件导出的 Netscape，路径由 DOUYIN_COOKIES 指定，默认 data/cookies/www.douyin.com_cookies.txt）
_COOKIE_FILE = Path(settings.DOUYIN_COOKIES)
_cookie_header_cache: Optional[str] = None

# aweme 结果缓存：aweme_id -> (monotonic_ts, raw)。probe 与 download 各打一次 detail API
# 会触发抖音频率限制（实测同 cookies 连续第二次必 403），同任务必须复用同一次请求结果。
_AWEME_TTL = 300  # 秒；成品直链有时效，过期重新解析
_aweme_cache: dict[str, tuple[float, dict]] = {}


# ============ 对外入口 ============
def is_douyin_url(url: str) -> bool:
    """域名判定：douyin.com / iesdouyin.com 及其子域。"""
    host = urlparse(url).hostname or ''
    return host == 'douyin.com' or host.endswith('.douyin.com') or host.endswith('.iesdouyin.com')


def is_douyin_user_url(url: str) -> bool:
    """是否作者主页链接（订阅源要求）：douyin.com/user/{sec_uid}。"""
    return bool(_SEC_UID_RE.search(url or ''))


async def probe(url: str) -> dict:
    """探测抖音视频元信息，返回与 ytdlp.probe 同构的 dict。"""
    return _normalize(await _fetch_aweme(url))


async def download(
    url: str,
    *,
    vid: str,
    on_progress: Optional[Callable[[dict], None]] = None,
    timeout: Optional[int] = None,
) -> dict:
    """流式下载成品到工作区（统一最佳画质），返回 {path, filename, size, ext}（与 ytdlp.download 同构）。

    url 可是两种：
    1. 抖音页面链接（含 /video/、/note/ 或 v.douyin.com 短链）→ 走 _fetch_aweme 拿 detail 再选 play_url；
    2. CDN 直链（host 含 douyinvod）→ 已经是可下载地址，跳过 detail API 直接下。
    """
    if _is_douyin_cdn_url(url):
        src = url
    else:
        aweme = await _fetch_aweme(url)
        src = _pick_play_url(aweme.get('video') or {})
    if not src:
        raise ProbeError('未取得下载地址（图集或不可见内容暂不支持）')
    dest = WORK_DIR / f'{vid}.mp4'
    await _download_to_file(src, dest, on_progress, timeout or settings.DOWNLOAD_TIMEOUT)
    # 直链可能返回错误页/过期 token 导致存下非视频文件（moov atom 缺失等），
    # 下完校验文件头，不合法时回退 detail API 重拿 play_url 再试一次
    if not _is_valid_video(dest):
        dest.unlink(missing_ok=True)
        # 直链可能过期/返回错误页，用 vid 拼视频页链接走 detail API 重拿 play_url
        aweme_url = f'https://www.douyin.com/video/{vid}'
        try:
            aweme = await _fetch_aweme(aweme_url)
            src2 = _pick_play_url(aweme.get('video') or {})
            if src2 and src2 != src:
                await _download_to_file(src2, dest, on_progress, timeout or settings.DOWNLOAD_TIMEOUT)
        except Exception:
            pass
    if not _is_valid_video(dest):
        raise ProbeError('下载的文件校验失败（非有效视频），请稍后重试')
    return {'path': str(dest), 'filename': dest.name, 'size': dest.stat().st_size, 'ext': dest.suffix.lstrip('.')}


async def probe_user(url: str) -> dict:
    """订阅时探测账号基本信息（轻量），走 fetch_user_profile 接口。

    不走 fetch_user_post_videos（那是采集列表用的），独立接口拿 nickname / avatar / aweme_count 等。
    返回字段与 Subscription 模型对齐，yt-dlp 渠道在 _sub_probe 里补 None。
    """
    m = _SEC_UID_RE.search(url)
    if not m:
        raise ProbeError('抖音订阅请粘贴作者主页链接（douyin.com/user/xxx）')
    handler = DouyinHandler(_handler_kwargs())
    try:
        profile = await handler.fetch_user_profile(m.group(1))
    except Exception as e:
        logger.warning('抖音账号探测失败 %s：%r', url, e)
        raise ProbeError(_friendly(str(e))) from e
    # logger.info('[douyin] fetch_user_profile 完整返回：\n%s', json.dumps(profile._to_raw(), ensure_ascii=False, default=str, indent=2))
    return {
        'name': profile.nickname,
        'extractor': 'Douyin',
        'avatar': profile.avatar_url,
        'total': profile.aweme_count,
        'signature': profile.signature,
        'follower_count': profile.follower_count,
        'total_favorited': profile.total_favorited,
        'unique_id': profile.unique_id,
        'ip_location': profile.ip_location,
        'city': profile.city,
        'country': profile.country,
        'room_id': profile.room_id,
        'mplatform_followers_count': profile.mplatform_followers_count,
    }


async def list_user_latest(url: str, limit: int) -> dict:
    """作者主页最新作品（「立即采集」时用），返回 {name, extractor, items:[{id,url,play_url,title,uploader,duration,thumbnail,extractor,publish_at}]}。

    只取作品列表第一页（新→旧），单次请求；字段与 _normalize 对齐，采集时无需再调独立接口补字段。
    """
    m = _SEC_UID_RE.search(url)
    if not m:
        raise ProbeError('抖音订阅请粘贴作者主页链接（douyin.com/user/xxx）')
    agen = DouyinHandler(_handler_kwargs()).fetch_user_post_videos(m.group(1), page_counts=limit, max_counts=limit)
    try:
        page = await agen.__anext__()
        raw = page._to_raw() or {}
    except StopAsyncIteration as e:
        raise ProbeError('抖音未返回作品列表（主页可能为空或风控）') from e
    except ProbeError:
        raise
    except Exception as e:
        logger.warning('抖音作品列表失败 %s：%r', url, e)
        raise ProbeError(_friendly(str(e))) from e
    finally:
        await agen.aclose()
    if raw.get('status_code') not in (0, None):
        raise ProbeError(f'抖音作品列表接口异常（status_code={raw.get("status_code")}）')
    # 调试：打印列表 API 完整返回
    # logger.info('[douyin] user_post 完整返回：\n%s', json.dumps(raw, ensure_ascii=False, default=str, indent=2))
    items = []
    name = None
    for aweme in (raw.get('aweme_list') or [])[:limit]:
        aid = aweme.get('aweme_id')
        if not aid:
            continue
        author = aweme.get('author') or {}
        name = name or author.get('nickname')
        video = aweme.get('video') or {}
        # url=主页链接（probe 用它提取 aweme_id）；play_url=真实 CDN 地址（下载用，省一次独立请求）
        play_urls = (video.get('play_addr') or {}).get('url_list') or []
        cover_urls = (video.get('cover') or {}).get('url_list') or []
        duration = video.get('duration')  # 抖音给毫秒
        items.append(
            {
                'id': aid,  # 平台视频 ID：去重 / vid 入库 / 订阅 diff 全靠它
                'url': f'https://www.douyin.com/video/{aid}',
                'play_url': play_urls[0] if play_urls else None,
                'title': aweme.get('desc'),
                'uploader': name,  # 与顶层 name 同值，collect() 传 info 入库时 create_from_info 需要
                'duration': duration / 1000 if duration else None,
                'thumbnail': cover_urls[0] if cover_urls else None,
                'extractor': 'Douyin',
                'publish_at': aweme.get('create_time'),
            }
        )
    return {'name': name, 'extractor': 'Douyin', 'items': items}


# ============ 探测链：URL → aweme 原始数据 → 统一结构 ============
def _handler_kwargs() -> dict:
    """f2 handler 统一配置：cookies/UA/禁代理（抖音请求直连，不受 HTTP_PROXY_URL 影响）。"""
    return {
        'cookie': _douyin_cookie_header(),
        'headers': {'User-Agent': _UA, 'Referer': 'https://www.douyin.com/'},
        'proxies': {'http://': None, 'https://': None},
        'timeout': 15,
        'max_retries': 2,
    }


async def _fetch_aweme(url: str) -> dict:
    """解析链接 → aweme_detail 原始 dict（带缓存，规避 detail API 频率限制）。"""
    m = _AWEME_ID_RE.search(url)
    if not m:
        # 短链（v.douyin.com/xxx）跟随 302 拿最终地址
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={'User-Agent': _UA}) as c:
                r = await c.get(url)
            m = _AWEME_ID_RE.search(str(r.url))
        except Exception as e:
            raise ProbeError(f'抖音短链解析失败：{e}') from e
        if not m:
            raise ProbeError('无法从链接中识别抖音视频 ID')
    aweme_id = m.group(1)
    hit = _aweme_cache.get(aweme_id)
    if hit and time.monotonic() - hit[0] < _AWEME_TTL:
        return hit[1]
    try:
        video = await DouyinHandler(_handler_kwargs()).fetch_one_video(aweme_id)
    except ProbeError:
        raise
    except Exception as e:
        logger.warning('抖音 detail API 失败 aweme_id=%s status=%s：%s', aweme_id, getattr(e, 'status_code', None), e)
        raise ProbeError(_friendly(str(e))) from e
    raw = video._to_raw()
    aweme = raw.get('aweme_detail') or raw
    if not aweme.get('video') and not aweme.get('music'):
        raise ProbeError('抖音未返回视频数据（图集或不可见内容暂不支持）')
    # 顺手清理过期项，防长驻进程缓慢膨胀
    if len(_aweme_cache) > 64:
        now = time.monotonic()
        for k in [k for k, (t, _) in _aweme_cache.items() if now - t >= _AWEME_TTL]:
            _aweme_cache.pop(k, None)
    _aweme_cache[aweme_id] = (time.monotonic(), aweme)
    return aweme


def _normalize(aweme: dict) -> dict:
    """aweme 原始数据 → 与 ytdlp 同构的探测结果。"""
    aweme_id = aweme.get('aweme_id')
    duration = aweme.get('duration')  # 抖音给的是毫秒
    # create_time 秒级时间戳 → UTC 秒
    ct = aweme.get('create_time')
    thumb = _first_url(((aweme.get('video') or {}).get('cover') or {}).get('url_list'))
    author = aweme.get('author') or {}
    avatar = _first_url((author.get('avatar_thumb') or {}).get('url_list'))

    return {
        'id': aweme_id,
        'title': aweme.get('desc'),
        'uploader': author.get('nickname'),
        'avatar': avatar,
        'duration': duration / 1000 if duration else None,
        'thumbnail': thumb,
        'extractor': 'Douyin',
        'webpage_url': f'https://www.douyin.com/video/{aweme_id}',
        'is_live': False,
        'qualities': _qualities(aweme),
        'play_url': _pick_play_url(aweme.get('video') or {}),
        'publish_at': int(ct) if ct else None,  # UTC 秒
    }


def _qualities(aweme: dict) -> list[dict]:
    """bit_rate 变体 → 画质列表（gear_name 形如 normal_1080_0），按高度降序。"""
    best: dict[int, dict] = {}
    for b in (aweme.get('video') or {}).get('bit_rate') or []:
        m = _GEAR_H_RE.search(b.get('gear_name') or '')
        if not m:
            continue
        height = int(m.group(1))
        best.setdefault(height, {'height': height, 'ext': 'mp4', 'vcodec': None, 'filesize': b.get('file_size_bytes')})
    return [best[h] for h in sorted(best, reverse=True)]


def _pick_play_url(video: dict) -> Optional[str]:
    """选最高码率档的直链（统一最佳画质）；无 bit_rate 变体时回退 play_addr。"""
    cands = []
    for b in video.get('bit_rate') or []:
        m = _GEAR_H_RE.search(b.get('gear_name') or '')
        u = _first_url((b.get('play_addr') or {}).get('url_list'))
        if m and u:
            cands.append((int(m.group(1)), u))
    if cands:
        return max(cands, key=lambda x: x[0])[1]
    return _first_url((video.get('play_addr') or {}).get('url_list'))


# ============ 下载链：直链 → 流式落盘 ============
async def _download_to_file(url: str, dest: Path, on_progress: Optional[Callable[[dict], None]], timeout: int) -> int:
    headers = {'User-Agent': _UA, 'Referer': 'https://www.douyin.com/'}
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
                async with client.stream('GET', url) as resp:
                    if resp.status_code != 200:
                        raise ProbeError(f'抖音 CDN 拉流失败（HTTP {resp.status_code}）')
                    total = int(resp.headers.get('content-length') or 0)
                    done = 0
                    started = time.monotonic()
                    with dest.open('wb') as f:
                        async for chunk in resp.aiter_bytes(256 * 1024):
                            f.write(chunk)
                            done += len(chunk)
                            if on_progress:
                                elapsed = time.monotonic() - started
                                # 起速后再报平均速率，避免首块虚高
                                speed = _fmt_speed(done / elapsed) if elapsed >= 0.5 else None
                                on_progress(
                                    {'percent': round(done / total * 100, 1) if total else None, 'speed': speed}
                                )
        if total and done < total:
            dest.unlink(missing_ok=True)
            raise ProbeError(f'下载不完整：期望 {total} 字节，实际 {done} 字节')
        return done
    except TimeoutError:
        dest.unlink(missing_ok=True)
        raise ProbeError('下载超时，请稍后重试')
    except asyncio.CancelledError:
        dest.unlink(missing_ok=True)
        raise


# ============ 工具 ============
def _douyin_cookie_header() -> str:
    """从 DOUYIN_COOKIES 指向的 Netscape 文件解析出 cookie 头（结果缓存到进程内）。"""
    global _cookie_header_cache
    if _cookie_header_cache is not None:
        return _cookie_header_cache
    _cookie_header_cache = _parse_netscape(_COOKIE_FILE) if _COOKIE_FILE.is_file() else ''
    if _cookie_header_cache:
        logger.info('抖音渠道 cookies 已从文件加载：%s', _COOKIE_FILE)
    else:
        logger.warning('抖音渠道未取到 cookies（文件缺失或无 douyin.com 行）：%s', _COOKIE_FILE)
    return _cookie_header_cache


def _parse_netscape(path: Path) -> str:
    """Netscape cookies.txt → 'k=v; k=v' 头，只取 douyin.com 域的行。"""
    pairs = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) < 7 or 'douyin.com' not in fields[0]:
            continue
        pairs.append(f'{fields[5]}={fields[6]}')
    return '; '.join(pairs)


def _first_url(url_list):
    return url_list[0] if url_list else None


def _is_douyin_cdn_url(url: str) -> bool:
    """判断 url 是否抖音 CDN 直链（跳过 detail API 直接下载）。"""
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        return False
    return 'douyinvod' in host


def _fmt_speed(bps: Optional[float]) -> Optional[str]:
    """字节/秒 → 人读速率串，对齐 yt-dlp 的 'x.xxMiB/s' 风格（前端直接展示）。"""
    if not bps:
        return None
    for unit in ('B/s', 'KiB/s', 'MiB/s', 'GiB/s'):
        if bps < 1024:
            return f'{bps:.2f}{unit}'
        bps /= 1024
    return f'{bps:.2f}TiB/s'


def _friendly(msg: str) -> str:
    """f2 异常文案归一。"""
    if '403' in msg:
        return '抖音风控拦截：请求过于频繁或 cookies 失效，请稍后重试；持续失败请更新 cookies（指纹配置见 app/conf/conf.yaml）'
    return (msg or '抖音解析失败').strip()[:200]


def _is_valid_video(path: Path) -> bool:
    """粗校验文件是不是有效视频：MP4 的 ftyp box 或 moov atom 开头，且大小 > 0。"""
    try:
        if path.stat().st_size < 1024:
            return False
        with open(path, 'rb') as f:
            head = f.read(64)
        # MP4: ... ftyp (offset 4-7)
        if b'ftyp' in head[:12]:
            return True
        # FLV: FLV (offset 0-2)
        if head[:3] == b'FLV':
            return True
        # WebM/MKV: 1A 45 DF A3
        if head[:4] == b'\x1a\x45\xdf\xa3':
            return True
        # CDN 返回错误页/JS 脚本等非媒体文件：HTML/XML/JS/JSON 都要拦住
        if head[:15].lstrip().lower().startswith((b'<html', b'<!doctype', b'<script', b'<body', b'{', b'<!--')):
            return False
        # 其它格式交由后续转写阶段判断，这里只拦住明显非媒体文件
        return True
    except OSError:
        return False
