"""向量/全文索引层：sqlite-vec（vec0）+ FTS5，与主库同文件，rowid 对齐 video_chunk.id。

Tortoise 不管虚表：挂扩展、建表、读写全走本文件裸 SQL；KNN/BM25 唯一调用方是 core/rag.py。
量级打满要换 pgvector（Postgres）时只替换本文件实现，上层接口不变。
"""

from typing import Optional

import sqlite_vec
from tortoise import Tortoise

from core.config import settings

_DIM = 1024  # bge-m3 输出维度（vec0 建表约束，与实际向量长度一致）


def _client():
    # 连接别名即 DB_TYPE（TORTOISE_ORM.connections 的 key），Tortoise 0.23 没有 'default' 别名
    return Tortoise.get_connection(settings.DB_TYPE)


def _ph(n: int) -> str:
    return ','.join(['?'] * n)


async def setup() -> None:
    """init_app 建表后调用：默认连接挂扩展（单连接一次即够）+ 建 vec0/FTS5 虚表，幂等。"""
    if settings.DB_TYPE != 'sqlite':  # 仅 SQLite 走本层；pg 路线换 pgvector 实现
        return
    client = _client()
    await client.create_connection(with_db=True)  # 幂等取已建连接
    raw = client._connection  # aiosqlite.Connection（Tortoise 单连接）
    await raw.enable_load_extension(True)
    await raw.load_extension(sqlite_vec.loadable_path())
    await raw.enable_load_extension(False)
    # vid 做 partition key：KNN 查询里辅助列（+col）不允许 WHERE 过滤（v0.1.9 实测报 illegal constraint），
    # 只有 partition key 支持 =/IN —— 用户库过滤（vids=用户采集集合）与 scope 过滤都靠它。
    # 块全局共享（媒体库模型），不再有 user 分区。
    await client.execute_script(
        "CREATE VIRTUAL TABLE IF NOT EXISTS video_chunk_fts USING fts5(text, tokenize='trigram');"
        'CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunk USING vec0('
        'chunk_id INTEGER PRIMARY KEY,'
        'vid TEXT PARTITION KEY,'
        f'embedding FLOAT[{_DIM}] distance_metric=cosine'
        ');'
    )


async def delete_vid(vid: str) -> int:
    """删该 vid 全部 vec/fts 索引行，返回条数。须在删 video_chunk 主表行之前调（依赖主表查 id）。"""
    client = _client()
    rows = await client.execute_query_dict('SELECT id FROM video_chunk WHERE vid = ?', [vid])
    for r in rows:
        await client.execute_query('DELETE FROM vec_chunk WHERE chunk_id = ?', [r['id']])
        await client.execute_query('DELETE FROM video_chunk_fts WHERE rowid = ?', [r['id']])
    return len(rows)


async def insert_chunks(rows: list[dict]) -> None:
    """主表写入后同步索引行。rows=[{id, vid, text, embedding(float32 BLOB)}]，id 三处对齐。"""
    client = _client()
    for r in rows:
        await client.execute_query('INSERT INTO video_chunk_fts(rowid, text) VALUES (?, ?)', [r['id'], r['text']])
        await client.execute_query(
            'INSERT INTO vec_chunk(chunk_id, embedding, vid) VALUES (?, ?, ?)',
            [r['id'], r['embedding'], r['vid']],
        )


async def knn(qvec: bytes, k: int, vids: Optional[list[str]] = None) -> list[tuple[int, float]]:
    """向量召回：cosine 最近邻 top-k，vids=用户库 vid 集合（媒体库全局共享，权限过滤在调用方）。

    vids=None 表示不限（内部/测试用）；返回 [(chunk_id, distance)]。
    """
    if vids is not None and not vids:
        return []
    sql = 'SELECT chunk_id, distance FROM vec_chunk WHERE embedding MATCH ? AND k = ?'
    vals: list = [qvec, k]
    if vids is not None:
        sql += f' AND vid IN ({_ph(len(vids))})'
        vals += list(vids)
    sql += ' ORDER BY distance'
    rows = await _client().execute_query_dict(sql, vals)
    return [(r['chunk_id'], r['distance']) for r in rows]


async def fts_match(query: str, k: int, vids: Optional[list[str]] = None) -> list[int]:
    """关键词召回（≥3 字走 trigram）：BM25 升序=最优在前。返回 chunk_ids。"""
    if vids is not None and not vids:
        return []
    phrase = '"' + query.replace('"', '""') + '"'  # 双引号按短语处理，内部引号转义
    sql = 'SELECT rowid AS cid FROM video_chunk_fts WHERE video_chunk_fts MATCH ?'
    vals: list = [phrase]
    if vids is not None:
        sql += f' AND rowid IN (SELECT id FROM video_chunk WHERE vid IN ({_ph(len(vids))}))'
        vals += list(vids)
    sql += ' ORDER BY bm25(video_chunk_fts) LIMIT ?'
    vals.append(k)
    rows = await _client().execute_query_dict(sql, vals)
    return [r['cid'] for r in rows]


async def like_match(query: str, k: int, vids: Optional[list[str]] = None) -> list[int]:
    """短查询（<3 字，trigram 不命中）兜底：子串 LIKE。返回 chunk_ids。"""
    if vids is not None and not vids:
        return []
    sql = 'SELECT id FROM video_chunk WHERE text LIKE ?'
    vals: list = [f'%{query}%']
    if vids is not None:
        sql += f' AND vid IN ({_ph(len(vids))})'
        vals += list(vids)
    sql += ' ORDER BY id DESC LIMIT ?'
    vals.append(k)
    rows = await _client().execute_query_dict(sql, vals)
    return [r['id'] for r in rows]
