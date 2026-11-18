"""HTTP Range 分段响应：让 <video> 可拖动进度条 / 按时间戳 seek。

starlette 0.37 的 FileResponse 不支持 Range（0.46+ 才加），浏览器 seek 发
`Range: bytes=x-` 拿到 200 全文件会回退到 0 秒重播。这里手写 206 分段：
解析单区间 → 流式读指定字节段 → Content-Range + Accept-Ranges。
"""

import re
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

_RANGE_RE = re.compile(r'bytes=(\d*)-(\d*)')
_CHUNK = 1024 * 1024  # 1MB 分块


def _parse_range(header: str, total: int) -> Optional[tuple[int, int]]:
    """解析单区间 Range 头 → (start, end) 闭区间；非法/多区间返回 None（回退全文件）。"""
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    start_s, end_s = m.groups()
    if not start_s and not end_s:
        return None
    if not start_s:  # 后缀区间：最后 N 字节
        start = max(0, total - int(end_s))
        end = total - 1
    else:
        start = int(start_s)
        end = min(int(end_s) if end_s else total - 1, total - 1)
    if start > end or start >= total:
        return None
    return start, end


def file_response(request: Request, path: Path, filename: str, media_type: str = 'video/mp4') -> Response:
    """按 Range 头返回 206 分段或 200 全文件；两种都带 Accept-Ranges 供浏览器 seek。"""
    total = path.stat().st_size
    inline = media_type.startswith('video/')
    disposition = f'{"inline" if inline else "attachment"}; filename="{filename}"'
    rng = request.headers.get('range')
    span = _parse_range(rng, total) if rng else None

    if rng and span is None:  # 有 Range 但解析失败/越界 → 416，让浏览器知道边界
        return Response(
            status_code=416,
            headers={'Content-Range': f'bytes */{total}', 'Accept-Ranges': 'bytes'},
        )

    def stream(start: int, end: int):
        with path.open('rb') as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                buf = f.read(min(_CHUNK, remaining))
                if not buf:
                    break
                remaining -= len(buf)
                yield buf

    if span:
        start, end = span
        headers = {
            'Content-Range': f'bytes {start}-{end}/{total}',
            'Content-Length': str(end - start + 1),
            'Accept-Ranges': 'bytes',
            'Content-Disposition': disposition,
        }
        return StreamingResponse(stream(start, end), status_code=206, headers=headers, media_type=media_type)

    headers = {
        'Content-Length': str(total),
        'Accept-Ranges': 'bytes',
        'Content-Disposition': disposition,
    }
    return StreamingResponse(stream(0, total - 1), status_code=200, headers=headers, media_type=media_type)
