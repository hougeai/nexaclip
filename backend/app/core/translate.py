"""视频翻译 / 双语字幕：转写分段 → LLM 整段翻译 → srt/ass 产物 → 千流云 TTS 逐句配音 → ffmpeg 换音烧录。

设计要点：
- 复用 transcript_segments（ASR 已给出句级时间轴），零新增转写成本
- 目标语言仅 zh/en（千流云 tts-flash 只有中英音色）；源语言可为任意，日/韩视频照样译成中英配音
- LLM 按分块批量翻译（每块带 idx，返回按 idx 对齐）——不逐句调用，一次一批
- 配音时轴对齐：每句 TTS 保持自然语速，按原句 start 绝对时点 adelay 叠加——不变速
  （变速人声发飘难听）、不截断（丢词），句长溢出自然延续到下一句开口前
- 配音：译音轨**完全替换**原音（两种语言人声叠放互相干扰，ducking 实测难听；
  译制片标准做法即替换）；画面只烧译文（原视频硬字幕抠不掉，译文放底部避开）
- srt 文件本身双语（译文主行 + 源文副行）：挂播放器可两语对照，不受硬字幕限制
"""

import asyncio
import re
from pathlib import Path
from typing import Optional

import httpx

from core.config import settings
from core.llm import chat as _chat
from core.llm import parse_json as _parse_json
from core.log import logger
from core.ytdlp import ProbeError

# 目标语种 → 显示名 + 千流云 tts-flash 候选音色（每语种男女声各一，首个为默认）。
# 前端选项经 GET /media/translate/langs 下发（单一真相源）；源语言可任意（langdetect 判），仅目标锁中英。
LANGS = {
    'zh': {
        'name': '中文',
        'voices': [
            {'id': 'zh-xiaoxia', 'label': '小夏 · 温柔女声'},
            {'id': 'zh-yunyang', 'label': '闻铮 · 新闻男声'},
        ],
    },
    'en': {
        'name': '英文',
        'voices': [
            {'id': 'en-annie', 'label': '安妮 · 女声'},
            {'id': 'en-gavin', 'label': '盖文 · 男声'},
        ],
    },
}
_BATCH_CHARS = 2500  # 每批翻译字符上限（LLM 输入规模控制）

_SYSTEM_TMPL = """你是专业视频字幕译者。把下面带序号的视频语音分块{source_hint}翻译成{target}。
要求：口语化、简洁（译文将合成配音并烧入画面单行字幕，每块尽量短）；保留专有名词原文；只翻内容不增删。
输入格式：[[chunk:N]] 文本
只输出 JSON，格式：{{"translations": [{{"idx": 0, "target": "..."}}]}}"""


async def translate_segments(segments: list[dict], lang_source: Optional[str], lang_target: str) -> list[dict]:
    """转写分段 → [{start, end, source, target}]（按时间序）。

    lang_source 为自动检测结果（None=判不出，LLM 自行识别原文）；失败抛 ProbeError。
    """
    check_pair(lang_source, lang_target)
    if not segments:
        raise ProbeError('无转写内容，无法翻译')
    batches: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    size = 0
    for i, s in enumerate(segments):
        text = (s.get('text') or '').strip()
        if not text:
            continue
        if cur and size + len(text) > _BATCH_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append((i, text))
        size += len(text)
    if cur:
        batches.append(cur)

    targets: dict[int, str] = {}
    system = _SYSTEM_TMPL.format(
        target=LANGS[lang_target]['name'],
        source_hint=f"（原文为{LANGS[lang_source]['name']}）" if lang_source in LANGS else '',
    )
    for batch in batches:
        user = '\n'.join(f'[[chunk:{i}]] {t}' for i, t in batch)
        raw = await _chat([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], temperature=0.2)
        parsed = _parse_json(raw)
        for item in parsed.get('translations') or []:
            idx = item.get('idx')
            if isinstance(idx, int) and 0 <= idx < len(segments):
                targets[idx] = str(item.get('target') or '').strip()
    pairs = [
        {'start': s['start'], 'end': s['end'], 'source': s['text'], 'target': targets.get(i, '')}
        for i, s in enumerate(segments)
        if (s.get('text') or '').strip()
    ]
    if not any(p['target'] for p in pairs):
        raise ProbeError('AI 未返回有效译文')
    return pairs


def check_pair(lang_source: Optional[str], lang_target: str) -> None:
    """校验目标语言合法且不与源语言相同。controller 提交配音/翻译前预检也用它。"""
    if lang_target not in LANGS:
        raise ProbeError('不支持的目标语言（当前仅 zh/en/ja/ko）')
    if lang_source == lang_target:
        raise ProbeError('目标语言与视频源语言相同，无需翻译')


def resolve_voice(lang_target: str, voice: Optional[str]) -> str:
    """校验音色属于目标语言并返回音色 ID；空 = 该语种默认（首个）。"""
    voices = LANGS[lang_target]['voices']
    if not voice:
        return voices[0]['id']
    if voice not in [v['id'] for v in voices]:
        raise ProbeError('音色与目标语言不匹配')
    return voice


# ============ 字幕产物（srt 双语下载 / ass 译文烧录） ============


def _ts_srt(sec: float) -> str:
    h, rem = divmod(max(0.0, sec), 3600)
    m, s = divmod(rem, 60)
    return f'{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}'


def _ts_ass(sec: float) -> str:
    h, rem = divmod(max(0.0, sec), 3600)
    m, s = divmod(rem, 60)
    return f'{int(h)}:{int(m):02d}:{int(s):02d}.{int((s % 1) * 100):02d}'


def build_srt(pairs: list[dict]) -> str:
    """双语 srt：译文主行 + 源文副行（播放器里两语对照）。"""
    blocks = []
    for n, p in enumerate(pairs, 1):
        text = f"{p['target']}\n{p['source']}" if p['target'] else p['source']
        blocks.append(f"{n}\n{_ts_srt(p['start'])} --> {_ts_srt(p['end'])}\n{text}\n")
    return '\n'.join(blocks)


# 烧录字幕分行：只按句末标点切，一句一条字幕——完整句子绝不拆碎
_SENT_END = re.compile(r'(?<=[。！？；!?])|(?<=\.)\s')  # 英文句号须跟空白，防误切小数/缩写


def split_lines(text: str) -> list[str]:
    """长译文 → 按句末标点切出的字幕行（时间按字数比例分摊在调用方做）。

    超长单句不硬切：ASS 播放分辨率下自动智能换行（WrapStyle 默认），
    宁可行内换行也不把一句话拦腰截断。
    """
    parts = [s.strip() for s in _SENT_END.split(text) if s and s.strip()]
    return parts or [text.strip()]


def build_ass(pairs: list[dict]) -> str:
    """烧录用 ass：只放译文，按标点切成短句随时间顺序逐条显示（不再整段糊屏）。

    每条短字幕的显示时长按字数在原句区间内比例分摊；单行大字号底部居中，
    MarginV 抬高避开原视频硬字幕。
    """
    header = (
        '[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n'
        '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Outline, Shadow, Alignment, MarginV\n'
        # 白字黑描边、底部居中（Alignment=2）、MarginV 抬高避开原硬字幕；长句 ASS 自动换行
        'Style: Default,Arial,48,&H00FFFFFF,&H00000000,1,2,10\n'
        '[Events]\nFormat: Layer, Start, End, Text\n'
    )
    events = []
    for p in pairs:
        if not p['target']:
            continue
        lines = split_lines(p['target'])
        total_chars = sum(len(x) for x in lines) or 1
        dur = max(0.5, p['end'] - p['start'])
        t = float(p['start'])
        for i, line in enumerate(lines):
            # 末条对齐到区间终点，避免比例累计误差留尾巴
            end = float(p['end']) if i == len(lines) - 1 else min(t + dur * len(line) / total_chars, float(p['end']))
            text = line.replace('{', '(').replace('}', ')')
            events.append(f'Dialogue: 0,{_ts_ass(t)},{_ts_ass(end)},{text}')
            t = end
    return header + '\n'.join(events) + '\n'


# ============ 配音 / 合成（千流云 TTS + ffmpeg，协程由 controller 编排） ============


async def tts_synth(text: str, voice: str, out_path: Path) -> None:
    """单句 TTS 合成（千流云 tts-flash，voice = resolve_voice 产出的音色 ID），落 out_path（mp3）。

    千流云单请求文本上限 2000 字符：ASR 句级分段一般远小于此，超长兜底截断并告警。失败抛 ProbeError。
    """
    if not settings.QL_KEY:
        raise ProbeError('未配置语音合成服务（.env 填 QL_KEY）')
    if len(text) > 2000:
        logger.warning('TTS 文本超 2000 字，截断合成：%d→2000', len(text))
        text = text[:2000]
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                settings.TTS_URL.rstrip('/') + '/audio/speech',
                headers={'Authorization': f'Bearer {settings.QL_KEY}'},
                json={'model': settings.TTS_MODEL, 'input': text, 'voice': voice},
            )
        if resp.status_code != 200 or not resp.content:
            raise ProbeError(f'配音合成失败：HTTP {resp.status_code} {resp.text[:200]}')
        out_path.write_bytes(resp.content)  # tts-flash 返回 audio/mpeg（mp3）
    except httpx.HTTPError as e:
        raise ProbeError(f'配音合成失败：{e}') from e
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise ProbeError('配音合成失败（空音频）')


async def mix_track(segs: list[tuple[Path, float]], out: Path, total: float) -> None:
    """各句配音按绝对起始秒 adelay 叠成整轨：自然语速不变形，句间空隙即静音。

    不变速——某句略长于原句时自然延续到下一句开口前（ASR 句间本有停顿余量）；
    空译文的句不叠（无需静音占位）；apad=whole_dur 补齐到视频总长，-t 截掉末句溢出尾巴。
    """
    if not segs:
        raise ProbeError('无可合成的配音内容')
    args = ['ffmpeg', '-y']
    filters = []
    for i, (p, start) in enumerate(segs):
        args += ['-i', str(p)]
        ms = int(start * 1000)
        filters.append(f'[{i}:a]aresample=24000,adelay={ms}|{ms}[a{i}]')
    if len(segs) == 1:
        mix = '[a0]'  # amix 最少两路，单句直通
    else:
        ins = ''.join(f'[a{i}]' for i in range(len(segs)))
        filters.append(f'{ins}amix=inputs={len(segs)}:duration=longest:normalize=0[mix]')
        mix = '[mix]'
    filters.append(f'{mix}apad=whole_dur={total:.3f}[out]')  # 短了补齐到视频总长（atrim 组合不可靠，实测提前 EOF）
    # -t 封顶：末句配音溢出视频结尾时截掉尾巴（长了截、短了补，两头都兜住）
    args += [
        '-filter_complex',
        ';'.join(filters),
        '-map',
        '[out]',
        '-t',
        f'{total:.3f}',
        '-ar',
        '24000',
        '-ac',
        '1',
        str(out),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        raise ProbeError('配音整轨合成超时')
    if proc.returncode != 0 or not out.exists():
        raise ProbeError(f'配音整轨合成失败：{(stderr or b"")[-300:].decode(errors="replace")}')


async def compose_video(src: Path, ass_path: Path, tts_wav: Path, out: Path) -> None:
    """一步成型：烧译文字幕 + 译音轨**完全替换**原音。

    不做原音垫底混音：两种语言的人声持续叠放互相干扰（实测难听）；
    译制片/YouTube 配音版的标准做法就是替换，想听原声看原片即可。
    """
    escaped = str(ass_path).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg',
        '-y',
        '-i',
        str(src),
        '-i',
        str(tts_wav),
        '-filter_complex',
        f"[0:v]subtitles='{escaped}'[v]",
        '-map',
        '[v]',
        '-map',
        '1:a',
        '-c:v',
        'libx264',
        '-preset',
        'veryfast',
        '-crf',
        '23',
        '-c:a',
        'aac',
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=1800)
    except asyncio.TimeoutError:
        proc.kill()
        raise ProbeError('配音视频合成超时')
    if proc.returncode != 0 or not out.exists():
        raise ProbeError(f'配音视频合成失败：{(stderr or b"")[-300:].decode(errors="replace")}')
