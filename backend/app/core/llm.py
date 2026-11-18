"""千流云 LLM 网关客户端：/chat/completions + /embeddings + /rerank（同一把 QL_KEY，OpenAI 兼容）。

失败语义分级：chat/embeddings 硬依赖（重试后仍失败抛 ProbeError）；
rerank 软依赖（失败返回 None，调用方降级，不阻塞出结果）。
"""

import asyncio
import json
import re
import struct
from typing import Optional

import httpx

from core.config import settings
from core.log import logger
from core.ytdlp import ProbeError

# embedding 批级重试次数（限流/瞬时网络错误间隔线性退避）
_ATTEMPTS = 3


# ============ 对话 ============


async def chat(messages: list[dict], *, temperature: float = 0.3) -> str:
    """调 OpenAI 兼容 /chat/completions（千流云）。失败抛 ProbeError。"""
    if not settings.QL_KEY:
        raise ProbeError('未配置 LLM 服务（.env 填 QL_KEY）')
    last = ''
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(
                    settings.LLM_URL.rstrip('/') + '/chat/completions',
                    headers={'Authorization': f'Bearer {settings.QL_KEY}'},
                    json={'model': settings.LLM_MODEL, 'messages': messages, 'temperature': temperature},
                )
            if resp.status_code != 200:
                detail = resp.text[:300]
                logger.warning('LLM 调用失败（%d/%d）：HTTP %s %s', attempt, _ATTEMPTS, resp.status_code, detail)
                raise ProbeError(f'AI 服务调用失败（HTTP {resp.status_code}）')
            data = resp.json()
            try:
                return data['choices'][0]['message']['content']
            except (KeyError, IndexError) as e:
                raise ProbeError('AI 服务返回异常') from e
        except (httpx.HTTPError, ProbeError) as e:
            last = str(e)
            logger.warning('LLM 调用异常（%d/%d）：%s', attempt, _ATTEMPTS, last)
            if attempt < _ATTEMPTS:
                await asyncio.sleep(settings.PROBE_RETRY_INTERVAL * attempt)
    raise ProbeError('AI 服务调用失败，请稍后重试')


def parse_json(text: str):
    """从 LLM 回复提取 JSON（容忍 ```json 围栏与前后杂文）。"""
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        raise ProbeError('AI 返回格式异常（未找到 JSON）')
    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        raise ProbeError('AI 返回格式异常（JSON 解析失败）') from e


def strip_fence(text: str) -> str:
    """去掉 Markdown 输出可能带的 ``` 围栏。"""
    return re.sub(r'^```[a-z]*\n?|```$', '', text.strip()).strip()


# ============ Embedding / 重排 ============


def pack(vec: list[float]) -> bytes:
    """float 列表 → float32 小端 BLOB（sqlite-vec 输入格式）。"""
    return struct.pack(f'<{len(vec)}f', *vec)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量 embedding（EMBED_BATCH 条/批）。返回与入参等长的向量列表。"""
    if not settings.QL_KEY:
        raise ProbeError('未配置 LLM 服务（.env 填 QL_KEY）')
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=600) as client:
        for i in range(0, len(texts), settings.EMBED_BATCH):
            batch = texts[i : i + settings.EMBED_BATCH]
            out.extend(await _embed_batch(client, batch))
    return out


async def embed_query(text: str) -> list[float]:
    """查询文本单条 embedding。"""
    return (await embed_texts([text]))[0]


async def _embed_batch(client: httpx.AsyncClient, batch: list[str]) -> list[list[float]]:
    last = ''
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            resp = await client.post(
                settings.LLM_URL.rstrip('/') + '/embeddings',
                headers={'Authorization': f'Bearer {settings.QL_KEY}'},
                json={'model': settings.EMBED_MODEL, 'input': batch},
            )
            if resp.status_code == 200:
                data = sorted(resp.json()['data'], key=lambda d: d['index'])
                return [d['embedding'] for d in data]
            last = f'HTTP {resp.status_code} {resp.text[:200]}'
        except httpx.HTTPError as e:
            last = str(e)
        logger.warning('embedding 调用失败（%d/%d）：%s', attempt, _ATTEMPTS, last)
        if attempt < _ATTEMPTS:
            await asyncio.sleep(settings.PROBE_RETRY_INTERVAL * attempt)
    raise ProbeError('Embedding 服务调用失败，请稍后重试')


async def rerank(query: str, docs: list[str], top_n: int) -> Optional[list[tuple[int, float]]]:
    """Cross-Encoder 重排 → [(原索引, 相关性分)] 按分降序；失败返回 None（调用方降级）。

    千流云 /rerank 为 Cohere 兼容形态：请求带 top_n（部分网关可能忽略），响应取
    results[].{index,relevance_score}；为兼容网关差异，结果本地再按分降序并截 top_n。
    """
    if not docs or not settings.QL_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                settings.LLM_URL.rstrip('/') + '/rerank',
                headers={'Authorization': f'Bearer {settings.QL_KEY}'},
                json={
                    'model': settings.RERANK_MODEL,
                    'query': query,
                    'documents': docs,
                    'top_n': top_n,
                    'return_documents': False,
                },
            )
        if resp.status_code != 200:
            logger.warning('rerank 失败：HTTP %s %s', resp.status_code, resp.text[:200])
            return None
        results = resp.json().get('results')
        if results is None:
            logger.warning('rerank 返回异常（无 results 字段）：%s', str(resp.json())[:200])
            return None
        ranked = [(r['index'], r['relevance_score']) for r in results]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:top_n]
    except Exception as e:
        logger.warning('rerank 异常（降级跳过）：%s', e)
        return None
