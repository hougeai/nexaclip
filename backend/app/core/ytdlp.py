"""yt-dlp 封装：探测走 Python API（extract_info），下载走子进程 CLI。

为什么两套：
- 探测用 Python API：省掉每次 0.3~0.5s 的进程启动开销，直接拿 dict 聚合格式，可复用已加载的 cookies（cookiefile）
- 下载用子进程：单任务崩溃/OOM 不波及主进程；取消 = kill 进程；--progress-template 输出易解析；进程级超时干净

格式选择按高度而非 format_id（后者有时效、跨 extractor 不通用）；优先 avc1+m4a 保证 remux 不转码。
"""

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

from .config import settings
from .log import logger
from .storage import WORK_DIR


class ProbeError(Exception):
    """探测/下载失败，msg 已是给人看的归一化文案（渠道共用：douyin 亦复用此类型）。"""


# ============ 通用 yt-dlp 选项 ============

_cookie_cache: Optional[str] = None
_cookie_cache_set = False


def _cookie_file() -> Optional[str]:
    """YouTube 用的 Netscape cookies 文件路径（settings.YOUTUBE_COOKIES，默认 data/cookies/www.youtube.com_cookies.txt）；
    文件不存在返回 None（公开视频不需要 cookies）。结果进程内缓存一次。"""
    global _cookie_cache, _cookie_cache_set
    if _cookie_cache_set:
        return _cookie_cache
    _cookie_cache_set = True
    p = Path(settings.YOUTUBE_COOKIES)
    _cookie_cache = str(p) if p.is_file() else None
    if _cookie_cache:
        logger.info('YouTube cookies 已从文件加载：%s', p)
    return _cookie_cache


def _common_opts() -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'skip_download': True,
        'socket_timeout': 30,
        'retries': 3,
    }
    cookie_file = _cookie_file()
    if cookie_file:
        opts['cookiefile'] = cookie_file
    if settings.HTTP_PROXY_URL:
        opts['proxy'] = settings.HTTP_PROXY_URL
    return opts


# ============ 探测 ============


def _aggregate_qualities(info: dict) -> list[dict]:
    """把 formats 聚合成按高度降序的画质选项（同高度优先 avc1，remux 不转码）。"""
    by_height: dict[int, dict] = {}
    for f in info.get('formats') or []:
        vcodec = f.get('vcodec') or 'none'
        height = f.get('height')
        if vcodec == 'none' or not height:
            continue
        size = f.get('filesize') or f.get('filesize_approx') or 0
        cur = by_height.get(height)
        if cur is None:
            by_height[height] = {**f, '_size': size}
        else:
            cur_avc = (cur.get('vcodec') or '').startswith('avc1')
            f_avc = vcodec.startswith('avc1')
            cur_size = cur.get('_size') or float('inf')
            if (f_avc and not cur_avc) or (f_avc == cur_avc and 0 < size < cur_size):
                by_height[height] = {**f, '_size': size}
    out = []
    for height in sorted(by_height, reverse=True):
        f = by_height[height]
        out.append(
            {
                'height': height,
                'ext': f.get('ext'),
                'vcodec': f.get('vcodec'),
                'filesize': f.get('filesize') or f.get('filesize_approx'),
            }
        )
    return out


def _normalize_info(info: dict) -> dict:
    """yt-dlp extract_info 结果 → 与 douyin 同构的探测结构。"""
    # 发布时间：timestamp（UTC 秒）或 upload_date（YYYYMMDD）；两者常只有其一
    ts = info.get('timestamp') or info.get('release_timestamp')
    if not ts and info.get('upload_date'):
        ts = datetime.strptime(info['upload_date'], '%Y%m%d').replace(tzinfo=timezone.utc).timestamp()
    return {
        'id': info.get('id'),
        'title': info.get('title'),
        'uploader': info.get('uploader'),
        'duration': info.get('duration'),
        'thumbnail': info.get('thumbnail'),
        'extractor': info.get('extractor'),
        'webpage_url': info.get('webpage_url'),
        'is_live': bool(info.get('is_live')),
        'qualities': _aggregate_qualities(info),
        'play_url': info.get('url'),  # 选定格式的直链（CDN 有时效，仅作快照）
        'publish_at': int(ts) if ts else None,  # UTC 秒
    }


async def probe(url: str) -> dict:
    """探测单个 URL 元信息。失败抛 ProbeError（文案已归一）。"""
    return await asyncio.to_thread(_probe_sync, url)


def _probe_sync(url: str) -> dict:
    try:
        with yt_dlp.YoutubeDL(_common_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise ProbeError(_friendly_error(str(e))) from e
    except Exception as e:
        raise ProbeError(_friendly_error(repr(e))) from e
    if not info:
        raise ProbeError('无法获取视频信息')
    return _normalize_info(info)


# ============ 频道枚举（订阅轮询用） ============


async def list_latest(url: str, limit: int) -> dict:
    """扁平化枚举频道/主页最新条目（extract_flat 不逐条解析详情，秒级）。

    返回 {name, extractor, total, items:[{url,title,publish_at}]}，与 douyin.list_user_latest 同构。
    total 取 playlist_count（部分频道页不返回，null 正常）。
    """
    return await asyncio.to_thread(_list_latest_sync, url, limit)


def _list_latest_sync(url: str, limit: int) -> dict:
    opts = {**_common_opts(), 'extract_flat': 'in_playlist'}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise ProbeError(_friendly_error(str(e))) from e
    except Exception as e:
        raise ProbeError(_friendly_error(repr(e))) from e
    if not info or info.get('_type') not in ('playlist', 'url'):
        raise ProbeError('这不是可枚举的频道/主页链接（请粘贴频道「视频」页地址）')
    items = []
    for e in info.get('entries') or []:
        if not e:
            continue
        u = e.get('url') or e.get('webpage_url')
        pid = e.get('id')
        if not u or not pid:
            continue
        items.append({'id': pid, 'url': u, 'title': e.get('title'), 'publish_at': e.get('timestamp')})
        if len(items) >= limit:
            break
    return {
        'name': info.get('uploader') or info.get('title') or info.get('channel'),
        'extractor': info.get('extractor_key'),
        'total': info.get('playlist_count'),
        'items': items,
    }


# ============ 下载 ============


def _best_format_string() -> str:
    """最佳画质格式串：优先 avc1+m4a（remux 不转码、移动端可播），逐级 fallback。"""
    return 'bv*[vcodec^=avc1]+ba[ext=m4a]/bv*+ba/b/b'


def _build_args(url: str, out_tmpl: str) -> list[str]:
    args = [
        'yt-dlp',
        '--no-playlist',
        '--newline',
        '--no-mtime',
        '--restrict-filenames',
        '-o',
        out_tmpl,
        # 进度逐行输出：已下字节|总字节|预估总字节|速度|ETA，供解析
        '--progress-template',
        'download:%(progress.downloaded_bytes)s|%(progress.total_bytes)s|'
        '%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s',
    ]
    cf = _cookie_file()
    if cf:
        args += ['--cookies', cf]
    if settings.HTTP_PROXY_URL:
        args += ['--proxy', settings.HTTP_PROXY_URL]

    args += ['-f', _best_format_string(), '--merge-output-format', 'mp4', '--embed-metadata']
    args.append(url)
    return args


def _to_int(x: str) -> Optional[int]:
    """把进度字段转 int；yt-dlp 对未知值会渲染 NA/None/空，一律当 None。"""
    x = x.strip()
    return int(float(x)) if x.replace('.', '', 1).isdigit() else None


def _to_str(x: str) -> Optional[str]:
    x = x.strip()
    return x if x and x not in ('NA', 'None') else None


def _parse_progress(line: str) -> Optional[dict]:
    """解析 --progress-template 输出的一行：已下|总|预估总|速度|ETA。

    非本模板行（yt-dlp 的 [download] xxx 等）不含 5 段竖线，返回 None 落到 tail。
    """
    parts = line.strip().split('|')
    if len(parts) != 5:
        return None
    d = _to_int(parts[0])
    t = _to_int(parts[1]) or _to_int(parts[2])
    # 至少要有一个可解析的字节数才认定是进度行
    if d is None and t is None:
        return None
    percent = round(d / t * 100, 1) if (d and t) else None
    return {
        'downloaded': d,
        'total': t,
        'percent': percent,
        'speed': _to_str(parts[3]),
        'eta': _to_str(parts[4]),
    }


async def download(
    url: str,
    *,
    vid: str,
    on_progress: Optional[Callable[[dict], None]] = None,
    timeout: Optional[int] = None,
) -> dict:
    """子进程下载成品到工作区（统一最佳画质），返回 {path, filename, size, ext}。

    on_progress(dict) 在每行进度时同步回调（写内存进度缓存）。超时/失败抛 ProbeError 并 kill 进程。
    """
    out_tmpl = str(WORK_DIR / f'{vid}.%(ext)s')
    args = _build_args(url, out_tmpl)
    timeout = timeout or settings.DOWNLOAD_TIMEOUT

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    tail: list[str] = []  # 保留最后若干行非进度输出，失败时用于错误归一
    try:
        async with asyncio.timeout(timeout):
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode('utf-8', 'ignore').rstrip()
                p = _parse_progress(line)
                if p:
                    if on_progress:
                        on_progress(p)
                elif line:
                    tail.append(line)
                    tail[:] = tail[-20:]
        rc = await proc.wait()
    except TimeoutError:
        _kill(proc)
        raise ProbeError('下载超时，请稍后重试')
    except asyncio.CancelledError:
        _kill(proc)
        raise

    if rc != 0:
        raise ProbeError(_friendly_error('\n'.join(tail)) or f'下载失败（退出码 {rc}）')

    produced = _find_output(WORK_DIR, vid)
    if not produced:
        raise ProbeError(_friendly_error('\n'.join(tail)) or '下载未产出文件')
    size = produced.stat().st_size
    return {
        'path': str(produced),
        'filename': produced.name,
        'size': size,
        'ext': produced.suffix.lstrip('.'),
    }


def _kill(proc: asyncio.subprocess.Process):
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def _find_output(work_dir: Path, vid: str) -> Optional[Path]:
    """yt-dlp 用 --restrict-filenames，成品即 {vid}.{ext}；取工作区里该前缀的最新文件。"""
    cands = sorted(work_dir.glob(f'{vid}.*'), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


# ============ 错误归一（放服务端统一维护，前端只展示） ============

_ERROR_RULES = [
    (
        r'Sign in to confirm you(?:\'|a)?re not a bot|confirm you.re not a bot',
        '该站点需要登录凭据，请联系管理员配置 cookies',
    ),
    (r'Video unavailable', '视频不可用（可能已删除、私密或地区限制）'),
    (r'Private video', '视频为私密，无法下载'),
    (r'Age restricted|age-restricted', '视频有年龄限制，需要登录凭据'),
    (r'is not a valid URL|Unsupported URL', '不支持的链接'),
    (
        r'HTTP Error 412|Precondition Failed',
        '该站点触发了 IP 风控（服务器出口 IP 被拦截），需住宅 IP 或代理，非链接问题',
    ),
    (r'HTTP Error 403', '访问被拒绝（403），可能需要凭据或代理'),
    (r'HTTP Error 404', '资源不存在（404）'),
    (r'ERROR:\s*(.+)', None),  # 兜底：抽取 ERROR: 后的原文
]


def _friendly_error(raw: str) -> str:
    """把 yt-dlp 原始 stderr 映射成人话。命中规则返回中文，否则回退原文首行。"""
    if not raw:
        return ''
    for pattern, msg in _ERROR_RULES:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            return msg or m.group(1).strip()
    for line in raw.splitlines():
        if 'ERROR' in line.upper():
            return line.strip()
    return raw.strip()[:200]
