"""AI 报告生成：转写分句 → LLM 大纲+章节 → LLM 图文报告。

标准「转写 → 大纲 → 报告」流水线，做两点关键简化：
- 不引入 camel-ai / sentence-transformers：LLM 一次结构化输出「大纲 + 章节时间范围」，
  跳过「向量匹配对话块→章节」环节（章节 start/end 直接取分块时间戳）
- 复用现有 transcript_segments，不重复转写
"""

from core.transcript import chunk_segments, fmt_ts as _ts
from core.llm import chat as _chat
from core.llm import parse_json as _parse_json
from core.llm import strip_fence as _strip_fence
from core.ytdlp import ProbeError

_OUTLINE_SYSTEM = """你是视频内容分析专家。根据带时间戳的语音分块，输出视频大纲与章节划分。
要求：
1. outline：Markdown 层级大纲，只用 # 和 ## 两级，概括视频逻辑结构，不要编造未出现的内容。
2. chapters：章节列表，title 对应大纲二级标题，start/end 为章节起止秒数（必须取自所覆盖语音分块的时间），summary 为 2-3 句章节摘要。
3. 章节必须按时间顺序、不重叠、覆盖全部分块。
只输出 JSON，格式：
{"outline": "...", "chapters": [{"title": "...", "start": 0, "end": 120, "summary": "..."}]}"""

_REPORT_SYSTEM = """你是技术写作专家。根据视频大纲与章节摘要，撰写一份结构清晰的中文图文报告（Markdown）。
要求：保留大纲层级结构（# / ##），每个章节展开 1-2 段说明，开头给一段全文导读。直接输出 Markdown，不要代码围栏。"""


async def analyze(segments: list[dict]) -> dict:
    """转写分句 → {outline, chapters, report}。失败抛 ProbeError（文案归一）。"""
    if not segments:
        raise ProbeError('无转写内容，无法生成报告')

    chunks = chunk_segments(segments)
    dialogue = _format_dialogue(chunks)

    # 1) 大纲 + 章节（一次结构化输出）
    outline_raw = await _chat(
        [
            {'role': 'system', 'content': _OUTLINE_SYSTEM},
            {'role': 'user', 'content': dialogue},
        ]
    )
    parsed = _parse_json(outline_raw)
    outline = (parsed.get('outline') or '').strip()
    chapters = [
        {
            'title': str(c.get('title') or '').strip(),
            'start': round(float(c.get('start') or 0), 2),
            'end': round(float(c.get('end') or 0), 2),
            'summary': str(c.get('summary') or '').strip(),
        }
        for c in (parsed.get('chapters') or [])
        if str(c.get('title') or '').strip()
    ]
    if not outline or not chapters:
        raise ProbeError('AI 未返回有效大纲/章节')

    # 2) 图文报告（基于大纲+章节摘要润色扩写）
    chapter_text = '\n'.join(
        f"## {c['title']}（{_ts(c['start'])} - {_ts(c['end'])}）\n{c['summary']}" for c in chapters
    )
    report = await _chat(
        [
            {'role': 'system', 'content': _REPORT_SYSTEM},
            {'role': 'user', 'content': f'视频大纲：\n{outline}\n\n章节摘要：\n{chapter_text}'},
        ]
    )
    report = _strip_fence(report)
    if not report:
        raise ProbeError('AI 未返回有效报告')
    return {'outline': outline, 'chapters': chapters, 'report': report}


def _format_dialogue(chunks: list[dict]) -> str:
    """分块 → 带序号与时间戳的对话文本（LLM 输入格式）。"""
    return '\n'.join(f"[[chunk:{i}]] [{_ts(c['start'])} --> {_ts(c['end'])}] {c['text']}" for i, c in enumerate(chunks))
