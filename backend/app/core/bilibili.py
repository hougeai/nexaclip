"""B站渠道：直接调公开 API（api.bilibili.com）探测与下载，不走 yt-dlp 网页抓取。

为什么独立渠道：yt-dlp 的 BiliBili extractor 走网页抓取（bilibili.com/video/BV... → HTML），
B站 WAF 对该路线直接 412（加 UA/Referer/cookies 都拦），但公开 API（api.bilibili.com）
可用——API 同样要求访客指纹：无 buvid3 一律 412（code=-412 request was banned），
故请求前经 finger/spi 匿名申领 buvid3/buvid4 并进程内缓存。单视频探测/下载无需登录；
订阅（空间页枚举）需 cookies + WBI 签名。

对外入口（与 douyin / ytdlp 同构）：
    is_bilibili_url(url)        是否 B 站链接
    is_bilibili_user_url(url)   是否 B 站空间页（订阅源要求）
    probe(url)                  探测元信息 → 与 ytdlp.probe 同构的 dict
    download(url, ...)          下载成品 → {path, filename, size, ext}
    probe_user(url)             订阅时探测账号信息（需 cookies）
    list_user_latest(url, ...)  空间页枚举最新作品（需 cookies）

cookies 来源（同 douyin）：浏览器插件导出的 Netscape txt，路径见 BILIBILI_COOKIES（默认 data/cookies/www.bilibili.com_cookies.txt）。
"""

import asyncio
import hashlib
import re
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from .config import settings
from .log import logger
from .storage import WORK_DIR
from .ytdlp import ProbeError

_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
_API_HEADERS = {
    'User-Agent': _UA,
    'Referer': 'https://www.bilibili.com/',
    'Accept': 'application/json',
}
_BV_RE = re.compile(r'BV[0-9A-Za-z]{8,12}')
_BVID_RE = re.compile(r'(?:bvid=|/video/)(BV[0-9A-Za-z]{8,12})', re.I)
_B23_RE = re.compile(r'b23\.tv/([A-Za-z0-9]+)', re.I)
_MID_RE = re.compile(r'space\.bilibili\.com/(\d+)')

# cookies 文件（浏览器插件导出的 Netscape，路径由 BILIBILI_COOKIES 指定，默认 data/cookies/www.bilibili.com_cookies.txt）
_COOKIE_FILE = Path(settings.BILIBILI_COOKIES)
_cookie_header_cache: Optional[str] = None

# B站 API 结果缓存：bvid -> (monotonic_ts, info_dict)。CDN 直链有时效，同任务复用同一次请求。
_INFO_TTL = 300  # 秒
_info_cache: dict[str, tuple[float, dict]] = {}

# WBI 签名：mixin key 从 nav API 派生，进程内缓存（B站定期轮换，失效时重取）
_MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    21,
    22,
    63,
    62,
    59,
    6,
    20,
    57,
    56,
    52,
    51,
    25,
    44,
    54,
    11,
    60,
    4,
    30,
    36,
    34,
]
_mixin_key_cache: Optional[str] = None


# ============ 对外入口 ============
def is_bilibili_url(url: str) -> bool:
    """域名判定：bilibili.com / b23.tv 及其子域。"""
    host = urlparse(url).hostname or ''
    return host == 'bilibili.com' or host.endswith('.bilibili.com') or host == 'b23.tv' or host.endswith('.b23.tv')


def is_bilibili_user_url(url: str) -> bool:
    """是否 B 站空间页（订阅源要求）：space.bilibili.com/{mid}。"""
    return bool(_MID_RE.search(url or ''))


async def probe(url: str) -> dict:
    """探测 B 站视频元信息，返回与 ytdlp.probe 同构的 dict。"""
    bvid = _extract_bvid(url)
    if not bvid:
        raise ProbeError('无法从链接中识别 B 站视频 ID（需 bilibili.com/video/BV... 或 b23.tv 短链）')
    info = await _fetch_video_info(bvid)
    return _normalize(info)


async def download(
    url: str,
    *,
    vid: str,
    on_progress: Optional[Callable[[dict], None]] = None,
    timeout: Optional[int] = None,
) -> dict:
    """流式下载 B 站成品到工作区，返回 {path, filename, size, ext}。

    流程：bvid → 视频信息 API 拿 cid → 播放 API 拿 CDN 直链 → 流式落盘。
    """
    bvid = _extract_bvid(url)
    if not bvid:
        raise ProbeError('无法从链接中识别 B 站视频 ID')
    info = await _fetch_video_info(bvid)
    cid = info.get('cid')
    if not cid:
        raise ProbeError('B 站未返回视频数据')
    src = await _fetch_play_url(bvid, cid)
    if not src:
        raise ProbeError('未取得 B 站下载地址')
    dest = WORK_DIR / f'{vid}.mp4'
    await _download_to_file(src, dest, on_progress, timeout or settings.DOWNLOAD_TIMEOUT)
    if not _is_valid_video(dest):
        dest.unlink(missing_ok=True)
        raise ProbeError('下载的文件校验失败（非有效视频），请稍后重试')
    return {'path': str(dest), 'filename': dest.name, 'size': dest.stat().st_size, 'ext': dest.suffix.lstrip('.')}


async def probe_user(url: str) -> dict:
    """订阅时探测账号基本信息（轻量），走 wbi/acc/info + relation/stat（需 cookies）。

    返回字段与 Subscription 模型对齐，对齐 douyin.probe_user。
    """
    mid = _extract_mid(url)
    if not mid:
        raise ProbeError('B站订阅请粘贴空间页链接（space.bilibili.com/xxx）')
    cookie = _bilibili_cookie_header()
    if not cookie:
        raise ProbeError(
            'B站订阅需登录 cookies：把浏览器导出的 Netscape 文件放 data/cookies/www.bilibili.com_cookies.txt（或设 BILIBILI_COOKIES 指向文件）'
        )
    headers = {**_API_HEADERS, 'Referer': 'https://space.bilibili.com/', 'Cookie': cookie}
    try:
        mixin = await _get_mixin_key(headers)
        info = await _wbi_get('https://api.bilibili.com/x/space/wbi/acc/info', {'mid': mid}, headers, mixin)
        stat = await _plain_get('https://api.bilibili.com/x/relation/stat', {'vmid': mid}, headers)
    except ProbeError:
        raise
    except Exception as e:
        logger.warning('B站账号探测失败 %s：%r', url, e)
        raise ProbeError(_friendly(str(e))) from e
    data = info.get('data') or {}
    follower = (stat.get('data') or {}).get('follower')
    return {
        'name': data.get('name'),
        'extractor': 'Bilibili',
        'avatar': _https(data.get('face')),
        'total': None,  # acc/info 不返回视频总数，arc/search 才有（枚举时刷新 video_count）
        'signature': data.get('sign'),
        'follower_count': follower,
        'total_favorited': None,
        'unique_id': mid,  # B站用户空间号（mid）
        'ip_location': None,  # acc/info 无 IP 属地
        'city': None,
        'country': None,
        'room_id': None,
        'mplatform_followers_count': follower,
    }


async def list_user_latest(url: str, limit: int) -> dict:
    """空间页最新作品（「立即采集」时用），走 wbi/arc/search（需 cookies）。

    返回 {name, extractor, items:[{id,url,title,uploader,duration,thumbnail,extractor,publish_at}]}，
    与 douyin.list_user_latest 同构。play_url 不返回（B站需逐条 playurl，采集时 download 内联拿）。
    """
    mid = _extract_mid(url)
    if not mid:
        raise ProbeError('B站订阅请粘贴空间页链接（space.bilibili.com/xxx）')
    cookie = _bilibili_cookie_header()
    if not cookie:
        raise ProbeError(
            'B站订阅需登录 cookies：把浏览器导出的 Netscape 文件放 data/cookies/www.bilibili.com_cookies.txt（或设 BILIBILI_COOKIES 指向文件）'
        )
    headers = {**_API_HEADERS, 'Referer': 'https://space.bilibili.com/', 'Cookie': cookie}
    try:
        mixin = await _get_mixin_key(headers)
        raw = await _wbi_get(
            'https://api.bilibili.com/x/space/wbi/arc/search',
            {'mid': mid, 'ps': str(limit), 'pn': '1', 'order': 'pubdate', 'platform': 'web'},
            headers,
            mixin,
        )
    except ProbeError:
        raise
    except Exception as e:
        logger.warning('B站作品列表失败 %s：%r', url, e)
        raise ProbeError(_friendly(str(e))) from e
    data = raw.get('data') or {}
    vlist = (data.get('list') or {}).get('vlist') or []
    name = None
    items = []
    for v in vlist[:limit]:
        bvid = v.get('bvid')
        if not bvid:
            continue
        name = name or v.get('author')
        items.append(
            {
                'id': bvid,  # 平台视频 ID：去重 / vid 入库 / 订阅 diff 全靠它
                'url': f'https://www.bilibili.com/video/{bvid}',
                'play_url': None,  # 采集时 download 内联调 playurl API
                'title': v.get('title'),
                'uploader': v.get('author'),
                'duration': _parse_length(v.get('length')),
                'thumbnail': _https(v.get('pic')),
                'extractor': 'Bilibili',
                'publish_at': v.get('created'),  # UTC 秒
            }
        )
    return {'name': name, 'extractor': 'Bilibili', 'items': items}


# ============ 探测链：URL → bvid → API 原始数据 ============
def _extract_bvid(url: str) -> Optional[str]:
    """从链接中提取 BV 号。支持 bilibili.com/video/BV... 和 b23.tv 短链。"""
    m = _BVID_RE.search(url)
    if m:
        return m.group(1)
    m = _B23_RE.search(url)
    if m:
        return m.group(1)  # b23.tv 后面就是 BV 号
    m = _BV_RE.search(url)
    if m:
        return m.group(0)
    return None


async def _fetch_video_info(bvid: str) -> dict:
    """调 B 站 view API → {title, bvid, cid, owner, duration, desc, cover}。带缓存。"""
    hit = _info_cache.get(bvid)
    if hit and time.monotonic() - hit[0] < _INFO_TTL:
        return hit[1]
    r = await _api_get('https://api.bilibili.com/x/web-interface/view', {'bvid': bvid})
    if r.status_code == 412:
        raise ProbeError('B 站风控拦截（HTTP 412）：访客指纹失效，请稍后重试或更新 cookies')
    if r.status_code != 200:
        raise ProbeError(f'B 站 API 请求失败（HTTP {r.status_code}）')
    raw = r.json()
    if raw.get('code') != 0:
        raise ProbeError(f'B 站 API 返回异常（code={raw.get("code")} {raw.get("message")}') from None
    data = raw['data']
    info = {
        'bvid': data.get('bvid'),
        'aid': data.get('aid'),
        'cid': data.get('cid'),
        'title': data.get('title'),
        'desc': data.get('desc'),
        'owner': (data.get('owner') or {}).get('name'),
        'duration': data.get('duration'),  # 秒（B站 view API 直接给秒，勿再除 1000）
        'cover': _https(data.get('pic')),
        'publish_at': data.get('pubdate'),  # UTC 秒
    }
    if len(_info_cache) > 64:
        now = time.monotonic()
        for k in [k for k, (t, _) in _info_cache.items() if now - t >= _INFO_TTL]:
            _info_cache.pop(k, None)
    _info_cache[bvid] = (time.monotonic(), info)
    return info


async def _fetch_play_url(bvid: str, cid: int) -> Optional[str]:
    """调 B 站 playurl API → CDN 直链。fnval 不传（实测传 16 返回 0 条 durl）。"""
    r = await _api_get(
        'https://api.bilibili.com/x/player/playurl',
        {'bvid': bvid, 'cid': cid, 'platform': 'web'},
    )
    if r.status_code == 412:
        raise ProbeError('B 站风控拦截（HTTP 412）：访客指纹失效，请稍后重试或更新 cookies')
    if r.status_code != 200:
        raise ProbeError(f'B 站播放 API 请求失败（HTTP {r.status_code}）')
    raw = r.json()
    if raw.get('code') != 0:
        raise ProbeError(f'B 站播放 API 返回异常（code={raw.get("code")} {raw.get("message")}') from None
    durls = (raw.get('data') or {}).get('durl') or []
    if not durls:
        return None
    return durls[0].get('url')


def _normalize(info: dict) -> dict:
    """B 站 API 结果 → 与 ytdlp 同构的探测结构。"""
    return {
        'id': info.get('bvid'),
        'title': info.get('title'),
        'uploader': info.get('owner'),
        'duration': info.get('duration') or None,  # API 已是秒，与 ytdlp/douyin 同构
        'thumbnail': info.get('cover'),
        'extractor': 'Bilibili',
        'webpage_url': f'https://www.bilibili.com/video/{info.get("bvid")}',
        'is_live': False,
        'qualities': [],
        'play_url': None,  # download() 时实时拿，不存探针结果
        'publish_at': info.get('publish_at'),
    }


# ============ 下载链：CDN 直链 → 流式落盘 ============
async def _download_to_file(url: str, dest: Path, on_progress, timeout: int) -> int:
    """流式下载 CDN 直链到 dest，返回字节数。"""
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(headers=_API_HEADERS, timeout=60, follow_redirects=True) as client:
                async with client.stream('GET', url) as resp:
                    if resp.status_code != 200:
                        raise ProbeError(f'B 站 CDN 拉流失败（HTTP {resp.status_code}）')
                    total = int(resp.headers.get('content-length') or 0)
                    done = 0
                    started = time.monotonic()
                    with dest.open('wb') as f:
                        async for chunk in resp.aiter_bytes(256 * 1024):
                            f.write(chunk)
                            done += len(chunk)
                            if on_progress:
                                elapsed = time.monotonic() - started
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
def _fmt_speed(bps: Optional[float]) -> Optional[str]:
    if not bps:
        return None
    for unit in ('B/s', 'KiB/s', 'MiB/s', 'GiB/s'):
        if bps < 1024:
            return f'{bps:.2f}{unit}'
        bps /= 1024
    return f'{bps:.2f}TiB/s'


def _is_valid_video(path: Path) -> bool:
    """粗校验文件是不是有效视频：MP4 ftyp / FLV / WebM 开头，且大小 > 0。"""
    try:
        if path.stat().st_size < 1024:
            return False
        with open(path, 'rb') as f:
            head = f.read(64)
        if b'ftyp' in head[:12]:
            return True
        if head[:3] == b'FLV':
            return True
        if head[:4] == b'\x1a\x45\xdf\xa3':
            return True
        if head[:15].lstrip().lower().startswith((b'<html', b'<!doctype', b'<script', b'<body', b'{', b'<!--')):
            return False
        return True
    except OSError:
        return False


def _extract_mid(url: str) -> Optional[str]:
    """从空间页链接提取 mid（用户 ID）：space.bilibili.com/{mid}。"""
    m = _MID_RE.search(url or '')
    return m.group(1) if m else None


def _https(url: Optional[str]) -> Optional[str]:
    """B站图片 API 返回 http:// 直链，HTTPS 页面下会触发 Mixed Content，统一升级为 https://。"""
    if url and url.startswith('http://'):
        return 'https://' + url[len('http://') :]
    return url


def _parse_length(s: Optional[str]) -> Optional[float]:
    """arc/search 的 length 是 'mm:ss' 或 'hh:mm:ss'，转秒。"""
    if not s:
        return None
    parts = s.split(':')
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return float(nums[0] * 60 + nums[1])
    if len(nums) == 3:
        return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
    return None


def _bilibili_cookie_header() -> str:
    """从 BILIBILI_COOKIES 指向的 Netscape 文件解析出 cookie 头（结果缓存到进程内）。"""
    global _cookie_header_cache
    if _cookie_header_cache is not None:
        return _cookie_header_cache
    _cookie_header_cache = _parse_netscape(_COOKIE_FILE) if _COOKIE_FILE.is_file() else ''
    if _cookie_header_cache:
        logger.info('B站渠道 cookies 已从文件加载：%s', _COOKIE_FILE)
    else:
        logger.warning('B站渠道未取到 cookies（文件缺失或无 bilibili.com 行），订阅功能将不可用：%s', _COOKIE_FILE)
    return _cookie_header_cache


def _parse_netscape(path: Path) -> str:
    """Netscape cookies.txt → 'k=v; k=v' 头，只取 bilibili.com 域的行。"""
    pairs = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) < 7 or 'bilibili.com' not in fields[0]:
            continue
        pairs.append(f'{fields[5]}={fields[6].strip()}')
    return '; '.join(pairs)


# 访客指纹：B 站 API 对无 cookie 请求一律 412（{"code":-412,"message":"request was banned"}），
# buvid3/buvid4 可由 finger/spi 匿名申领，进程内缓存复用。
_buvid_cache: Optional[tuple[str, str]] = None


async def _get_buvid() -> tuple[str, str]:
    """申领 buvid3/buvid4（finger/spi，无需登录）。失败返回空串。"""
    global _buvid_cache
    if _buvid_cache:
        return _buvid_cache
    try:
        async with httpx.AsyncClient(headers=_API_HEADERS, timeout=10) as c:
            r = await c.get('https://api.bilibili.com/x/frontend/finger/spi')
        data = r.json().get('data') or {}
        b3, b4 = str(data.get('b_3') or ''), str(data.get('b_4') or '')
        if b3:
            _buvid_cache = (b3, b4)
            return _buvid_cache
        logger.warning('B 站 buvid 申领返回空：%s', r.text[:120])
    except Exception as e:  # noqa: BLE001
        logger.warning('B 站 buvid 申领失败：%r', e)
    return '', ''


async def _api_headers(referer: Optional[str] = None) -> dict:
    """API 请求头：用户 cookies（若有）+ 缺失时补匿名 buvid3/4，用于过 B 站 412 门禁。"""
    cookie = _bilibili_cookie_header()
    if 'buvid3=' not in cookie:
        b3, b4 = await _get_buvid()
        if b3:
            extra = f'buvid3={b3}' + (f'; buvid4={b4}' if b4 else '')
            cookie = f'{cookie}; {extra}' if cookie else extra
    headers = dict(_API_HEADERS)
    if cookie:
        headers['Cookie'] = cookie
    if referer:
        headers['Referer'] = referer
    return headers


async def _api_get(url: str, params: dict, timeout: int = 15) -> httpx.Response:
    """带访客 cookie 的 GET；命中 412 说明 buvid 可能已失效，重领一次再试。"""
    global _buvid_cache
    r = None
    for _attempt in (1, 2):
        async with httpx.AsyncClient(headers=await _api_headers(), timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, params=params)
        if r.status_code != 412:
            return r
        _buvid_cache = None
    return r


async def _get_mixin_key(headers: dict) -> str:
    """从 nav API 取 wbi_img 双 key，派生 mixin_key（进程内缓存，B站定期轮换）。"""
    global _mixin_key_cache
    if _mixin_key_cache:
        return _mixin_key_cache
    async with httpx.AsyncClient(headers=headers, timeout=15) as c:
        r = await c.get('https://api.bilibili.com/x/web-interface/nav')
    data = (r.json().get('data') or {}).get('wbi_img') or {}
    img_key = (data.get('img_url') or '').rsplit('/', 1)[-1].split('.')[0]
    sub_key = (data.get('sub_url') or '').rsplit('/', 1)[-1].split('.')[0]
    if not img_key or not sub_key:
        raise ProbeError('B站 WBI key 获取失败（nav API 无 wbi_img）')
    raw = img_key + sub_key
    _mixin_key_cache = ''.join(raw[i] for i in _MIXIN_KEY_ENC_TAB)[:32]
    return _mixin_key_cache


def _wbi_sign(params: dict, mixin_key: str) -> dict:
    """给 params 加 wts + w_rid 签名。返回新 dict。"""
    signed = dict(params)
    signed['wts'] = str(int(time.time()))
    cleaned = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in sorted(signed.items())}
    query = urllib.parse.urlencode(cleaned)
    signed['w_rid'] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return signed


async def _wbi_get(url: str, params: dict, headers: dict, mixin_key: str) -> dict:
    """带 WBI 签名的 GET。code!=0 抛 ProbeError（-403 时清 mixin 缓存重试一次）。"""
    global _mixin_key_cache
    async with httpx.AsyncClient(headers=headers, timeout=15) as c:
        r = await c.get(url, params=_wbi_sign(params, mixin_key))
    raw = r.json()
    if raw.get('code') == -403 and _mixin_key_cache:
        # mixin key 可能已轮换，清缓存重取后重试一次
        _mixin_key_cache = None
        fresh = await _get_mixin_key(headers)
        async with httpx.AsyncClient(headers=headers, timeout=15) as c:
            r = await c.get(url, params=_wbi_sign(params, fresh))
        raw = r.json()
    if raw.get('code') != 0:
        raise ProbeError(_friendly(f'B站接口异常 code={raw.get("code")} {raw.get("message")}'))
    return raw


async def _plain_get(url: str, params: dict, headers: dict) -> dict:
    """无签名 GET（relation/stat 等公开接口）。"""
    async with httpx.AsyncClient(headers=headers, timeout=15) as c:
        r = await c.get(url, params=params)
    raw = r.json()
    if raw.get('code') != 0:
        raise ProbeError(_friendly(f'B站接口异常 code={raw.get("code")} {raw.get("message")}'))
    return raw


def _friendly(msg: str) -> str:
    """B站异常文案归一。"""
    if '-799' in msg or '频繁' in msg:
        return 'B站风控拦截：请求过于频繁或 cookies 失效，请稍后重试；持续失败请更新 cookies'
    if '412' in msg:
        return 'B站拦截：请检查 cookies 是否有效'
    return (msg or 'B站解析失败').strip()[:200]
