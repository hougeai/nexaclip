"""本地成品存储：下载成品永久留在 data/store/，下载统一走 /api/v1/media/{vid}/file 路由。

路径形态：{store_dir}/{vid}.{ext} —— vid 是平台视频 ID，全局唯一，文件天然全局共享一份；
用户归属在 user_vid 关联表，删记录按引用计数决定是否清文件。
高光切片是派生产物，落 {store_dir}/clips/{vid}/，随原视频删除一并回收。
"""

import asyncio
import shutil
import zipfile
from pathlib import Path
from typing import Optional

from .config import settings
from .log import logger

STORE_DIR = Path(settings.DATA_DIR) / 'store'
WORK_DIR = Path(settings.DATA_DIR) / 'work'
CLIPS_DIR = STORE_DIR / 'clips'


def ensure_dirs() -> None:
    """启动时确保数据目录存在（store/ 成品留存、work/ 下载中转），缺失即创建。"""
    for d in (STORE_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def local_path(vid: str) -> Optional[Path]:
    """按 vid 找成品文件（命名 {vid}.{ext}，ext 随来源/上传容器而变，故 glob 匹配）。"""
    for p in STORE_DIR.glob(f'{vid}.*'):
        if p.is_file():
            return p
    return None


async def save(local_path_: str, *, vid: str) -> None:
    """把成品从工作区移到 store/，永久留存。目录缺失时兜底补建（防手工删库目录后落盘失败）。"""
    ext = Path(local_path_).suffix
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STORE_DIR / f'{vid}{ext}'
    shutil.move(local_path_, dest)
    logger.info('成品留存：%s', dest.name)


def derived_path(vid: str, kind: str) -> Optional[Path]:
    """翻译派生产物路径：kind=sub（双语 srt）/ ass（烧录用译文字幕）/ dub（配音视频）。
    命名 {vid}_{kind}.{ext}：下划线分隔，local_path 的 '{vid}.*' glob 不会误匹配（实测）。"""
    ext = {'sub': 'srt', 'ass': 'ass', 'dub': 'mp4'}[kind]
    p = STORE_DIR / f'{vid}_{kind}.{ext}'
    return p if p.is_file() else None


def delete(vid: str) -> None:
    """删除成品文件、高光切片与翻译派生产物（库条目最后一个引用消失时调用）。"""
    p = local_path(vid)
    if p:
        p.unlink(missing_ok=True)
    shutil.rmtree(CLIPS_DIR / vid, ignore_errors=True)
    for suffix in ('_sub.srt', '_ass.ass', '_dub.mp4'):
        (STORE_DIR / f'{vid}{suffix}').unlink(missing_ok=True)


def clip_path(vid: str, name: str) -> Optional[Path]:
    """按文件名取切片（限定在该 vid 的切片目录内，防路径穿越）。"""
    safe = Path(name).name
    p = CLIPS_DIR / vid / safe
    return p if p.is_file() else None


async def cut_clips(src: Path, vid: str, spans: list[tuple[float, float]], *, merge: bool) -> str:
    """从成品按时间区间切 mp4，返回可直接下载的产物文件名。

    -c copy 零转码（关键帧对齐，秒级完成）：
    - merge=True：各切一段再 concat 拼成一个「高光集」mp4
    - merge=False：各切一段打包成 zip（多文件走 zip，避免浏览器逐个下载的拦截与打扰）
    切片目录每次先清空重建，避免旧产物堆积。
    """
    out_dir = CLIPS_DIR / vid
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, (start, end) in enumerate(spans):
        part = out_dir / f'part{i}.mp4'
        await _cut(src, part, start, max(0.5, end - start))
        parts.append(part)

    if merge:
        merged = out_dir / 'highlights.mp4'
        await _concat(parts, merged)
        for part in parts:
            part.unlink(missing_ok=True)
        return merged.name

    archive = out_dir / 'highlights.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED) as zf:  # mp4 已压缩，无需再压
        for i, part in enumerate(parts):
            zf.write(part, arcname=f'highlight-{i + 1}.mp4')
    for part in parts:
        part.unlink(missing_ok=True)
    return archive.name


async def _cut(src: Path, out: Path, start: float, duration: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg',
        '-y',
        '-ss',
        f'{start:.3f}',
        '-i',
        str(src),
        '-t',
        f'{duration:.3f}',
        '-c',
        'copy',
        '-avoid_negative_ts',
        'make_zero',
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    # start 超时长时 ffmpeg 返回 0 但产出空文件，size 校验兜底
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        tail = (stderr or b'')[-400:].decode(errors='replace')
        logger.warning('高光切片失败 %s：%s', out.name, tail)
        raise ValueError('切片失败，时间点可能超出视频时长')


async def _concat(parts: list[Path], out: Path) -> None:
    """concat demuxer 拼接（-c copy，要求各段编码一致——同源切出必满足）。"""
    listing = out.parent / 'concat.txt'
    listing.write_text('\n'.join(f"file '{p.name}'" for p in parts), encoding='utf-8')
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg',
        '-y',
        '-f',
        'concat',
        '-safe',
        '0',
        '-i',
        str(listing),
        '-c',
        'copy',
        str(out),
        cwd=str(out.parent),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    listing.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.exists():
        tail = (stderr or b'')[-400:].decode(errors='replace')
        logger.warning('高光拼接失败：%s', tail)
        raise ValueError('高光拼接失败，请改用分条导出')
