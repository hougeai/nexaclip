"""时间轴高光标注：转写分块 → 一次 LLM 调用挑出值得看的点 → [{start,end,type,label,reason}]。

设计要点：
- 复用 transcript.chunk_segments 压缩输入（与 ai_report 同构），不重复转写
- LLM 只输出分块序号 idx，start/end 由代码从真实分块时间戳取——杜绝时间幻觉
- type 限定四类（金句/冲突/核心概念/转折），前端按类型着色；非法值丢弃
- 一次调用出结果，不做多轮；数量 3-8 条，宁缺毋滥
"""

from core.llm import chat as _chat
from core.llm import parse_json as _parse_json
from core.transcript import chunk_segments, fmt_ts as _ts
from core.ytdlp import ProbeError
from models.enums import HighlightType

_TYPES = {t.value for t in HighlightType}

_SYSTEM = """你是视频内容剪辑专家。下面是一段带时间戳的视频语音分块，每块前有序号 [[chunk:N]]。
请挑出 3-8 个「最值得回看」的时间点，供用户在时间轴上快速定位。

每个点标注：
1. idx：该点对应的分块序号（必须是已存在的序号）。
2. type：只能是 {"quote"(金句), "conflict"(冲突/争议), "concept"(核心概念), "turn"(转折)} 之一。
3. label：一句话点题（金句类直接摘录原话，其余用短语概括），不超过 30 字。
4. reason：一句话说明为什么值得看，不超过 40 字。

要求：按时间顺序、不重复、宁缺毋滥（内容平淡就少标）。只输出 JSON，格式：
{"highlights": [{"idx": 0, "type": "quote", "label": "...", "reason": "..."}]}"""


async def score(segments: list[dict]) -> list[dict]:
    """转写分句 → 高亮点列表 [{start,end,type,label,reason}]。失败抛 ProbeError。"""
    if not segments:
        raise ProbeError('无转写内容，无法标注高光')
    chunks = chunk_segments(segments)
    if not chunks:
        raise ProbeError('无转写内容，无法标注高光')

    dialogue = '\n'.join(
        f"[[chunk:{i}]] [{_ts(c['start'])} --> {_ts(c['end'])}] {c['text']}" for i, c in enumerate(chunks)
    )
    raw = await _chat(
        [
            {'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content': dialogue},
        ]
    )
    parsed = _parse_json(raw)
    out: list[dict] = []
    for h in parsed.get('highlights') or []:
        idx = h.get('idx')
        htype = str(h.get('type') or '').strip()
        label = str(h.get('label') or '').strip()
        if not isinstance(idx, int) or not (0 <= idx < len(chunks)):
            continue  # 序号越界/非法，丢弃该点
        if htype not in _TYPES or not label:
            continue
        c = chunks[idx]
        out.append(
            {
                'start': round(float(c['start']), 2),
                'end': round(float(c['end']), 2),
                'type': htype,
                'label': label[:100],
                'reason': str(h.get('reason') or '').strip()[:200],
            }
        )
    if not out:
        raise ProbeError('AI 未标注出有效高光点')
    out.sort(key=lambda x: x['start'])
    return out
