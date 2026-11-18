"""文案转写：ffmpeg 抽音频 → 千流云 asr-pro（/audio/transcriptions）。

直接吃 store/ 里的本地成品文件，不回源站二次拉流。ffmpeg 提取 16kHz 单声道 mp3——
ASR 输入的标准形态（64k 码率，1 小时约 28MB）。asr-pro 配 verbose_json 返回句级
segments，start/end 是全产品时间轴命脉。输出偶带 emoji 标记，用正则清理。
"""

import asyncio
import re
from pathlib import Path
from typing import Optional

import httpx

from core.config import settings
from core.log import logger
from core.storage import STORE_DIR, local_path
from core.ytdlp import ProbeError

# ASR 单文件上限（asr-pro：≤50MB 且 ≤60 分钟）。绝大多数视频整发一次即可，仅在超限时
# 按 _SEGMENT_SECONDS 分段（比上限少 10 分钟，防边界抖动），单段失败退避重试。
_MAX_AUDIO_SIZE = 50 * 1024 * 1024
_MAX_AUDIO_DURATION = 3600
_SEGMENT_SECONDS = 3000
_ATTEMPTS = 2

# 部分 ASR 会在转写结果里混入 emoji（如 🙂、🔥），转写文案只留纯文本
_EMOJI_PATTERN = re.compile(
    '['
    '\U0001f600-\U0001f64f'
    '\U0001f300-\U0001f5ff'
    '\U0001f680-\U0001f6ff'
    '\U0001f700-\U0001f77f'
    '\U0001f780-\U0001f7ff'
    '\U0001f800-\U0001f8ff'
    '\U0001f900-\U0001f9ff'
    '\U0001fa00-\U0001fa6f'
    '\U0001fa70-\U0001faff'
    '\U00002702-\U000027b0'
    '+]',
    flags=re.UNICODE,
)


async def extract_from_store(vid: str) -> dict:
    """对 store/ 里的成品做音频转写，返回 {text, segments}。失败抛 ProbeError（文案已归一）。

    超 ASR 单文件限制（50MB/1h）时 ffmpeg 切段逐段转写再拼接，分段转写的时间戳统一加上
    前段累计偏移，保证全片时间轴连续。
    """
    if not local_path(vid):
        raise ProbeError('成品文件不存在，无法转写')

    audio = await _extract_audio(vid)
    try:
        parts = await _split_audio(audio)
        texts: list[str] = []
        segments: list[dict] = []
        offset = 0.0
        try:
            for part in parts:
                result = await _transcribe(part)
                texts.append(result['text'])
                for seg in result['segments']:
                    segments.append(
                        {**seg, 'start': round(seg['start'] + offset, 2), 'end': round(seg['end'] + offset, 2)}
                    )
                offset += await _audio_duration(part)
        finally:
            for part in parts:
                if part != audio:
                    part.unlink(missing_ok=True)
        return {'text': ''.join(texts), 'segments': segments}
    finally:
        audio.unlink(missing_ok=True)  # 中间音频用完即删，不留工作区垃圾


async def _extract_audio(vid: str) -> Path:
    """ffmpeg 抽 16kHz 单声道 mp3，落工作区（转写完即删）。"""
    src = local_path(vid)
    out = STORE_DIR.parent / 'work' / f'{vid}.mp3'
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg',
        '-y',  # 覆盖旧中间件
        '-i',
        str(src),
        '-vn',  # 去视频轨
        '-ac',
        '1',  # 单声道
        '-ar',
        '16000',  # 16kHz，ASR 标准采样率
        '-b:a',
        '64k',
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    if proc.returncode != 0 or not out.exists():
        tail = (stderr or b'')[-400:].decode(errors='replace')
        logger.warning('音频提取失败 %s：%s', vid, tail)
        raise ProbeError('音频提取失败，成品可能不含音轨')
    return out


async def _split_audio(audio: Path) -> list[Path]:
    """超限音频按 _SEGMENT_SECONDS 切段（-c copy 零转码，秒级完成），返回分段路径。"""
    duration = await _audio_duration(audio)
    if duration <= _MAX_AUDIO_DURATION and audio.stat().st_size <= _MAX_AUDIO_SIZE:
        return [audio]
    work_dir = audio.parent
    segments: list[Path] = []
    start = 0
    while start < duration:
        seg = work_dir / f'{audio.stem}_part{len(segments)}.mp3'
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg',
            '-y',
            '-ss',
            str(start),
            '-t',
            str(_SEGMENT_SECONDS),
            '-i',
            str(audio),
            '-c',
            'copy',
            str(seg),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0 or not seg.exists():
            raise ProbeError('音频分段失败')
        segments.append(seg)
        start += _SEGMENT_SECONDS
    logger.info('音频 %.0fs，已分 %d 段转写', duration, len(segments))
    return segments


async def _audio_duration(audio: Path) -> float:
    """ffprobe 读时长；失败按 0 处理（仅靠大小判断是否分段）。"""
    proc = await asyncio.create_subprocess_exec(
        'ffprobe',
        '-v',
        'error',
        '-show_entries',
        'format=duration',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        str(audio),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.strip())
    except ValueError:
        return 0.0


async def _transcribe(audio: Path) -> dict:
    """调千流云 /audio/transcriptions（asr-pro + verbose_json）。

    返回 {text, segments: [{start, end, text}]}；未配置 key 时抛错提示。
    超时/限流/5xx 单段重试（线性退避）；4xx 参数或鉴权错误直接失败不空转。
    """
    if not settings.QL_KEY:
        raise ProbeError('未配置语音识别服务（.env 填 QL_KEY）')

    body = audio.read_bytes()
    last = ''
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.ASR_TIMEOUT) as client:
                resp = await client.post(
                    settings.ASR_URL.rstrip('/') + '/audio/transcriptions',
                    headers={'accept': 'application/json', 'Authorization': f'Bearer {settings.QL_KEY}'},
                    files={
                        'file': (audio.name, body, 'audio/mpeg'),
                        'model': (None, settings.ASR_MODEL),
                        'response_format': (None, 'verbose_json'),
                    },
                )
            if resp.status_code == 200:
                return _parse_asr(resp.json())
            last = f'HTTP {resp.status_code} {resp.text[:200]}'
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                break
        except httpx.HTTPError as e:
            last = str(e) or repr(e)
        logger.warning('ASR 转写失败 %s（%d/%d）：%s', audio.stem, attempt, _ATTEMPTS, last)
        if attempt < _ATTEMPTS:
            await asyncio.sleep(5 * attempt)
    logger.warning('ASR 转写最终失败 %s：%s', audio.stem, last)
    raise ProbeError('语音识别失败，请稍后重试')


def _parse_asr(data: dict) -> dict:
    """ASR verbose_json 响应 → {text, segments}。

    只取 segment 的 start/end/text；顶层 text 弃用，改由 segments 重建纯文稿。
    """
    segments = [
        {
            'start': round(float(s.get('start') or 0), 2),
            'end': round(float(s.get('end') or 0), 2),
            'text': _EMOJI_PATTERN.sub('', s.get('text') or '').strip(),
        }
        for s in (data.get('segments') or [])
        if (s.get('text') or '').strip()
    ]
    text = _EMOJI_PATTERN.sub('', ''.join(s['text'] for s in segments)).strip()
    if not text and not segments:
        raise ProbeError('未识别到语音内容')
    return {'text': text, 'segments': segments}


# ============ 分句 → 语义分块（AI 报告与向量索引共用，全项目唯一一处切块逻辑） ============

# 分块参数（经验值，对齐参考 ASRProcessor）：200 字 ≈ 10~30s 语音，检索单元小而准、
# 引用跳转粒度细；答案上下文偏薄的问题由 rag.ask 的 ±1 邻块扩展（small-to-big）解决，
# 不靠放大块尺寸——块变大会混合多主题稀释向量、跳转粒度也变粗。
_CHUNK_GAP_SECONDS = 3
_CHUNK_MAX_CHARS = 200


def chunk_segments(segments: list[dict]) -> list[dict]:
    """句子 → 分块：间隔过大或字数超限即断开（无说话人维度）。"""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for seg in segments:
        if cur and (
            seg['start'] - cur[-1]['end'] > _CHUNK_GAP_SECONDS
            or sum(len(s['text']) for s in cur) + len(seg['text']) > _CHUNK_MAX_CHARS
        ):
            chunks.append(cur)
            cur = []
        cur.append(seg)
    if cur:
        chunks.append(cur)
    return [{'start': c[0]['start'], 'end': c[-1]['end'], 'text': ''.join(s['text'] for s in c)} for c in chunks]


def fmt_ts(sec: Optional[float]) -> str:
    """秒 → mm:ss。"""
    m, s = divmod(int(sec or 0), 60)
    return f'{m:02d}:{s:02d}'
