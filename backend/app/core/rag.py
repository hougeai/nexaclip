"""RAG 读路径：混合检索（向量 + 关键词 → RRF → 重排）+ 跨视频问答（改写 + [n] 引用）。

检索与问答是同一条读路径，合在一文件；存储层（vec0/FTS5 裸 SQL）在 core/vectorstore.py。
同步接口：LLM 秒级返回，无需异步状态机（区别于报告链路）。
"""

import re
from typing import Optional

from core import llm, vectorstore
from core.config import settings
from core.log import logger
from core.transcript import fmt_ts
from models.video import UserVid, Video, VideoChunk


# ============ 混合检索：多路召回 → RRF 融合 → 重排 ============


async def search(
    user_id: str, query: str, *, top_k: int = settings.FINAL_TOPK, scope_vids: Optional[list[str]] = None
) -> list[dict]:
    """检索用户库内片段。媒体库全局共享，权限=vid 集合过滤：用户 UserVid ∩ scope_vids。

    scope_vids：None=用户全库；空列表=范围内无视频（直接空结果）。
    """
    query = query.strip()
    if not query or (scope_vids is not None and not scope_vids):
        return []
    user_vids = set(await UserVid.filter(user_id=user_id).values_list('vid', flat=True))
    vids = list(user_vids & set(scope_vids)) if scope_vids is not None else list(user_vids)
    if not vids:
        return []

    # 1) 多路召回：向量（语义）+ 关键词（专有名词兜底）
    qvec = llm.pack(await llm.embed_query(query))
    vec_ids = await vectorstore.knn(qvec, settings.RECALL_TOPK, vids=vids)
    if len(query) >= 3:
        kw_ids = await vectorstore.fts_match(query, settings.RECALL_TOPK, vids=vids)
    else:  # trigram 对 2 字查询不命中（实测），走 LIKE 兜底
        kw_ids = await vectorstore.like_match(query, settings.RECALL_TOPK, vids=vids)

    # 2) RRF 融合：只用排名，两路分数量纲免归一
    fused: dict[int, float] = {}
    for rank, (cid, _) in enumerate(vec_ids):
        fused[cid] = fused.get(cid, 0.0) + 1 / (settings.RRF_K + rank + 1)
    for rank, cid in enumerate(kw_ids):
        fused[cid] = fused.get(cid, 0.0) + 1 / (settings.RRF_K + rank + 1)
    if not fused:
        return []
    cand_ids = sorted(fused, key=fused.get, reverse=True)[: settings.FUSE_TOPK]

    # 3) 取块正文并 join video 补标题/账号（块全局共享，权限已由 vids 保证；块可能已被删，按实取）
    chunks = await VideoChunk.filter(id__in=cand_ids).all()
    if not chunks:
        return []
    videos = {v.vid: v for v in await Video.filter(vid__in=[c.vid for c in chunks]).all()}
    items = [
        {
            'vid': c.vid,
            'seq': c.seq,
            'start': c.start,
            'end': c.end,
            'text': c.text,
            'score': round(fused[c.id], 5),
            'title': videos[c.vid].title if c.vid in videos else None,
            'uploader': videos[c.vid].uploader if c.vid in videos else None,
            'thumbnail': videos[c.vid].thumbnail if c.vid in videos else None,
            'extractor': videos[c.vid].extractor if c.vid in videos else None,
            'duration': videos[c.vid].duration if c.vid in videos else None,
            'channel': videos[c.vid].channel if c.vid in videos else None,
            'publish_at': videos[c.vid].publish_at if c.vid in videos else None,
        }
        for c in sorted(chunks, key=lambda c: fused[c.id], reverse=True)
    ]

    # 4) 重排（软依赖：失败保持 RRF 序）
    ranked = await llm.rerank(query, [i['text'] for i in items], min(top_k, len(items)))
    if ranked is not None:
        items = [{**items[i], 'score': round(s, 5)} for i, s in ranked]
    return items[:top_k]


# ============ 问答：query 改写 → 检索 → 片段作答 ============

_REWRITE_SYSTEM = """你是检索问题改写助手。根据对话历史，把用户最新问题改写为独立完整的中文检索问题：消解「它/这个/那个/后者」等指代与省略，只保留问题本身。
只输出一行改写后的问题，不要解释、不要引号。"""

_ASK_SYSTEM = """你是个人视频库问答助手。只依据给出的视频转写片段回答用户问题：
1. 每个结论句末标注引用编号，如 [1] 或 [2][3]，编号只能使用所提供的片段。
2. 严禁编造片段之外的内容；片段不足以回答时明确说明缺什么，并建议补采哪类视频。
3. 片段行首「·章节：」是该片段在源视频中的章节主题，仅用于理解语境，不作为引用内容。
4. 中文 Markdown 输出，简洁有条理，不要代码围栏。"""


async def ask(
    user_id: str,
    question: str,
    *,
    history: Optional[list[dict]] = None,
    scope_vids: Optional[list[str]] = None,
) -> dict:
    """返回 {answer, rewritten, citations:[{n,vid,title,start,end,snippet}]}。"""
    question = question.strip()
    rewritten = await _rewrite(question, history or []) or question
    chunks = await search(user_id, rewritten, top_k=settings.FINAL_TOPK, scope_vids=scope_vids)
    if not chunks:
        return {
            'answer': '库里还没有检索到相关内容：可以先采集相关视频，或换个说法再问。',
            'rewritten': rewritten,
            'citations': [],
        }
    # small-to-big：检索单元保持精准（≤200 字小块），给 LLM 的上下文扩展命中块 ±1 邻块，
    # 避免断句边界把一句话劈两半导致答案缺前因后果；引用编号/跳转仍锚定命中块本身
    vids = list({c['vid'] for c in chunks})
    need = {(c['vid'], s) for c in chunks for s in (c['seq'] - 1, c['seq'], c['seq'] + 1)}
    rows = await VideoChunk.filter(vid__in=vids, seq__in=sorted({s for _, s in need})).all()
    texts = {(r.vid, r.seq): r.text for r in rows if (r.vid, r.seq) in need}
    # 轻量层级：命中块 start 反查所属章节标题（章节按时间不重叠，取 start≤块的最后一章），
    # 拼进上下文给 LLM 主题框架——同一段话在不同章节含义不同；不改索引、只多一次 video 查询
    chapter_map = await _chapter_titles(vids)
    context = '\n\n'.join(
        f"[{i + 1}]《{c['title'] or c['vid']}》{_chapter_label(chapter_map.get(c['vid']), c['start'])} "
        f"{fmt_ts(c['start'])}-{fmt_ts(c['end'])}："
        + ''.join(texts.get((c['vid'], s), '') for s in (c['seq'] - 1, c['seq'], c['seq'] + 1))
        for i, c in enumerate(chunks)
    )
    raw = await llm.chat(
        [
            {'role': 'system', 'content': _ASK_SYSTEM},
            {'role': 'user', 'content': f'片段：\n{context}\n\n问题：{question}'},
        ]
    )
    answer = llm.strip_fence(raw)
    # 引用校验：只保留答案里真实出现且命中片段的编号
    cited = {int(n) for n in re.findall(r'\[(\d+)\]', answer)}
    citations = [
        {
            'n': i + 1,
            'vid': c['vid'],
            'title': c['title'],
            'start': c['start'],
            'end': c['end'],
            'snippet': c['text'][:80],
        }
        for i, c in enumerate(chunks)
        if i + 1 in cited
    ]
    return {'answer': answer, 'rewritten': rewritten, 'citations': citations}


async def _chapter_titles(vids: list[str]) -> dict[str, list[dict]]:
    """vid → 该视频章节列表（按 start 升序）。一次查齐，供命中块反查所属章节；无章节的跳过。"""
    out: dict[str, list[dict]] = {}
    videos = await Video.filter(vid__in=vids, ai_chapters__not_isnull=True).all()
    for v in videos:
        if v.ai_chapters:
            out[v.vid] = sorted(v.ai_chapters, key=lambda c: c.get('start') or 0)
    return out


def _chapter_label(chs: Optional[list[dict]], sec: float) -> str:
    """块起始秒 → 「·章节：xxx」上下文标注；章节按时间不重叠，取 start≤sec 的最后一章即所属。
    无章节（未生成/解析失败）返回空串，上下文退化为原格式。"""
    if not chs:
        return ''
    hit = None
    for c in chs:
        if (c.get('start') or 0) <= sec:
            hit = c
        else:
            break
    title = (hit or chs[0]).get('title')
    return f'·章节：{title}' if title else ''


async def _rewrite(question: str, history: list[dict]) -> Optional[str]:
    """多轮指代消解；异常/空输出降级返回 None（按原问题检索）。"""
    if not history:
        return None
    try:
        lines = '\n'.join(f"问：{h.get('q') or ''}\n答：{(h.get('a') or '')[:200]}" for h in history[-3:])
        raw = await llm.chat(
            [
                {'role': 'system', 'content': _REWRITE_SYSTEM},
                {'role': 'user', 'content': f'对话历史：\n{lines}\n\n最新问题：{question}'},
            ],
            temperature=0,
        )
        out = raw.strip().splitlines()[0][:200] if raw.strip() else ''
        return out or None
    except Exception:
        logger.exception('问题改写失败，按原问题检索')
        return None
