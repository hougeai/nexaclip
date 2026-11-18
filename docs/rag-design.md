# 方向一实施设计：全库语义检索 + 视频问答（RAG）

> 由 `roadmap.md`「方向一」展开。原则不变：千流云同一把 `QL_KEY`、零本地重依赖、单文件 SQLite、不新增供应商。
> 向量库选型 **sqlite-vec**（嵌入式、与主库同文件），已在本机双路径实测通过（v0.1.9，stdlib sqlite3 + aiosqlite/Tortoise，KNN/partition key/辅助列全可用）。

## 0. 范围裁剪

| 做 | 不做（理由） |
| --- | --- |
| 转写后自动切片入库（向量 + 全文双索引） | 语义话题边界分块：沿用现有句感知分块（间隔 >3s 或累计 >200 字断开），块粒度已够引用用，升级列后续 |
| 混合检索：向量 + BM25 → RRF 融合 → rerank | 服务端向量库（Qdrant/Milvus/Weaviate）：不引入独立进程与第二存储，嵌入式 sqlite-vec 已覆盖 |
| 跨视频问答，答案带 `[n]` 引用 → 点击跳原视频 seek | 说话人分离、MCP 端点：主链路跑通后另议 |
| 存量一键回填（reindex） | 多租户隔离强化：媒体库全局共享，权限=UserVid 归属集合过滤（vid IN） |

## 1. 数据流总览

```
采集 → 下载 → 转写(已有)
                ├→ LLM 报告(已有，不动)      ┐ 转写成功后
                └→ 切片 → embedding → 入库(新) ┘ 两任务并行，互不依赖

提问 → (多轮则先 query 改写)
     → 向量召回 top30 ∥ BM25/LIKE 召回 top30
     → RRF 融合 top20
     → bge-reranker-v2-m3 重排 top8
     → 上下文 = 命中块 ±1 邻块(small-to-big) + 章节标题反查(层级)
     → LLM 基于片段作答（句末标 [n]）
     → 前端渲染引用 chips → 点击开弹窗 seek 到 start 秒
```

## 2. 数据模型（三张表各管一域，rowid 对齐）

| 表 | 管理者 | 存什么 |
| --- | --- | --- |
| `video_chunk` | Tortoise 模型 | 文本 + 时间戳，**唯一事实源**（正文、start/end、模型指纹） |
| `video_chunk_fts` | 裸 SQL（FTS5） | 全文倒排（BM25 关键词召回） |
| `vec_chunk` | 裸 SQL（sqlite-vec） | 向量（KNN 语义召回） |

### 2.1 新表 `video_chunk`（`models/video.py` 内，与 `Video` 同文件）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| vid | CharField(100), index | 归属视频（平台视频 ID，全局共享库） |
| seq | IntField | 块序号（0 起） |
| text | TextField | 块正文（≤200 字） |
| start / end | FloatField | 块起止秒（句级时间戳透传，引用跳转的根基） |
| model | CharField(100) | embedding 模型指纹，换模型时校验并重建 |

`Meta: table='video_chunk', indexes=(('vid','seq'),)`。**向量不落此表**——由 `vec_chunk` 独管，避免双份存储。
块全员共享（媒体库模型：一个视频一份块），用户权限在检索层按 `UserVid` 解析出的 vid 集合过滤。

### 2.2 FTS5 虚拟表（启动时建）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS video_chunk_fts USING fts5(
    text, tokenize = 'trigram'
);
```

- **rowid 与 `video_chunk.id` 对齐**，独立表（不用 external-content，免触发器）；写入/删除由代码显式同步——chunk 只有两个写点（索引重建、视频删除），显式维护比触发器透明。
- trigram 已在本机 SQLite 3.45.1 实测：**≥3 字查询命中，2 字查询返回空** → 短查询走 LIKE 兜底（§4.2）。

### 2.3 vec0 虚表（sqlite-vec，启动时建）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunk USING vec0(
    chunk_id    INTEGER PRIMARY KEY,              -- = video_chunk.id
    vid         TEXT    PARTITION KEY,            -- 用户库过滤（vids=UserVid 解析集合）与 scope 过滤都靠它
                                                  --（实测 v0.1.9：KNN 查询里辅助列 +col 不允许 WHERE，
                                                  -- 只有 partition key 支持 =/IN）
    embedding   FLOAT[1024] distance_metric=cosine
);
```

- 向量序列化：float32 小端 BLOB `struct.pack('<1024f', *vec)`，bge-m3 输出 1024 维；
- 扩展挂载在 `init_app` 建表后一次性执行（每连接一次）：

```python
import sqlite_vec
raw = await Tortoise.get_connection('default').acquire_connection()  # aiosqlite.Connection
await raw.enable_load_extension(True)
await raw.load_extension(sqlite_vec.loadable_path())
```

### 2.4 `Video` 表加 2 列

`embed_status`（none/running/done/failed，新增枚举 `EmbedStatus`）+ `embed_error`。与 `ai_status` 完全同构，不发明新状态机。

> 表结构变更一律**清库重建**（`generate_schemas` 自动建全），不做存量迁移。

## 3. 写入路径（切片入库）

### 3.1 分块复用

切块函数 `chunk_segments()` 在 `core/transcript.py:225`（转写域：音频→分句→分块，一文件覆盖整条链路），`ai_report` 与索引共用——**全项目只此一处切块逻辑**。200 字块 ≈ 40-60s 语音，bge-m3 上限 8192 token，远不触顶。

### 3.2 编排改动（`controllers/video.py`）

`_transcribe` 成功那次 save 同时置 `ai_status=running` + `embed_status=running`，然后**并行**起 `_ai_analyze(vid)` 与 `_embed(vid)`（两者只依赖 segments，互不依赖）。

`_embed(vid)`：
1. 读 segments → `chunk_segments()` 切块；
2. 分批（32 块/批）调 `/embeddings`（`core/llm.py`，批级线性退避重试 3 次，仿 `_probe_retry`）；
3. 幂等替换（顺序：vec → fts → 主表，先删后插）：
   - `DELETE FROM vec_chunk WHERE vid = ?`、`DELETE FROM video_chunk_fts WHERE rowid IN (SELECT id FROM video_chunk WHERE vid = ?)`、`VideoChunk.filter(vid).delete()`；
   - 逐块 insert 主表拿回 id → 用同 id 写 fts(rowid) 与 vec_chunk(chunk_id)；
4. 置 `embed_status=done`；异常归一落 `embed_error`，全程不冒泡（对齐 `_ai_analyze` 写法）。

### 3.3 存量回填与级联删除

- `POST /kb/reindex`：扫本用户 `status=ready 且 transcript 非空 且 embed_status≠done(或 model 不匹配)` 的视频，逐个起 `_embed`，立即返回计数。前端「知识库」页顶部一键按钮。
- `controllers/video.remove()`：删视频时连带删其 vec/fts/chunk 三处（同 §3.2 顺序）。
- `init_app._recover_orphan_tasks`：`embed_status=running` 标 failed（与 ai 同款）。

## 4. 检索（`core/rag.py`）

### 4.1 向量召回（sqlite-vec KNN）

```sql
SELECT chunk_id, distance FROM vec_chunk
WHERE embedding MATCH ? AND k = 30
  AND vid IN (...)         -- partition key：用户库（UserVid 解析）∩ scope，媒体库全局共享权限在此收敛
ORDER BY distance;
```

- 一条 SQL 完成「取近邻 + 权限过滤」，无自建缓存、无失效逻辑；
- `distance`（cosine）转 score 后与 BM25 路做 RRF（融合只用排名，天然免归一）；
- 返回 top30 `[(chunk_id, score)]`。

### 4.2 关键词召回

- 查询 ≥3 字：FTS5 BM25 —— `SELECT rowid, bm25(video_chunk_fts) s FROM video_chunk_fts WHERE video_chunk_fts MATCH ? ORDER BY s LIMIT 30`（查询串双引号包裹按短语处理，内部引号转义）。
- 查询 <3 字：`WHERE text LIKE '%q%'` 兜底（中文双字专有名词是常态，此路必须留）。

### 4.3 融合与重排

- **RRF**：`score = Σ 1/(60 + rank_i)`，两路各取排名，融合后 top20。
- **Rerank**：POST `/rerank`（`bge-reranker-v2-m3`，同 key），query + 20 段 text → `relevance_score` 取 top8。rerank 调用失败**降级跳过**，不阻塞出结果。
- 结果行 join video 表补元信息（title/uploader/thumbnail/extractor/duration/channel/publish_at，检索卡片直接渲染封面与角标），统一返回：

```json
{ "chunks": [ { "vid", "seq", "start", "end", "text", "score", "title", "uploader",
                "thumbnail", "extractor", "duration", "channel", "publish_at" } ] }
```

## 5. 问答（`core/rag.py::ask` + `controllers/video.py`）

- **Query 改写**：`history` 非空时先一次轻量 LLM 调用，把「它/这个方法/第二段」改写成独立完整问题再检索；单轮跳过。
- **上下文构建（small-to-big）**：检索单元保持小而准（≤200 字块），但作答前把每个命中块**扩展 ±1 邻块**（`seq-1/seq/seq+1`，一条 `VideoChunk` 批量查询取回），避免断句边界把一句话劈两半导致答案缺前因后果；引用编号 / 跳转仍锚定命中块本身，不受邻块影响。
- **章节层级（轻量增强）**：命中块 `start` 反查所属 `ai_chapters` 章节标题（章节按时间不重叠，取 `start≤块秒` 的最后一章，纯内存线性扫；不改索引结构、只多一次 video 查询），拼进上下文作 `·章节：xxx` 给 LLM 主题框架。报告未生成的视频自动降级为无标注，检索照常。
- **作答**：top8 片段编号注入 prompt（`[3]《标题》·章节：索引优化 03:12-04:05：正文（含±1邻块）`），system 约束「只依据片段回答、句末标 [n]、片段不足要明说并建议补采哪些视频、章节标注仅供理解语境不作引用」。输出 Markdown。
- **引用校验**：解析答案中的 `[n]`，只保留命中片段，citations 附 vid/start/end。
- **同步接口**：LLM 秒级返回，无需异步状态机（区别于报告链路）。

## 6. API（并入 `api/v1/media.py`，挂 `/api/v1/media/kb`）

| 端点 | 入参 | 出参 |
| --- | --- | --- |
| `POST /media/kb/search` | `{q, scope:{platform?,uploader?}, top_k?}` | `{chunks:[...]}`（§4.3） |
| `POST /media/kb/ask` | `{question, history?:[{q,a}], scope?}` | `{answer, rewritten?, citations:[{n,vid,title,start,end,snippet}]}` |
| `POST /media/kb/reindex` | — | `{queued:n}`（本次补索引视频数） |
| `GET /media/kb/status` | — | `{indexed, pending, failed, total_transcribed}`（前端进度/提示） |

鉴权沿用路由级 `DependAuth` + `CTX_USER_ID`，响应沿用 `Success/Fail`。`to_out` lite 增 `embed_status`（弹窗角标用）。

## 7. 前端

- **新页 `views/console/KnowledgeView.vue`**，路由 `/app/kb`（name `kb`），ConsoleLayout 左导航加「知识库」。单页双模切换：
  - **搜片段**：输入框 + 结果列表（视频标题·账号·`mm:ss` 时间 + 片段摘录，命中词高亮），点击 → 开弹窗 seek；
  - **问知识库**：对话面板（多轮，Markdown 渲染复用 `marked`），答案内 `[n]` 渲染为引用 chip（`《标题》 03:12`），点击 → 开弹窗 seek；
  - scope 下拉复用 `historyGroups` 数据；顶部「一键补索引」按钮 + `kb/status` 进度提示。
- **`VideoDetailModal` 扩展**：新增 `initial_seek` prop，复用既有「`readyState<1` 挂 `loadedmetadata` 再 seek」机制（Range 已打通，直接可跳）。
- **stepper 不加第 5 节点**（索引是后台细节，避免 UI 噪音）。失败续跑分两条路：详情弹窗/采集卡片文案 Tab 里 `transcript_error` 显示「重新转写」（`POST /media/{vid}/retranscribe`，基于本地成品只重跑转写链）；向量索引失败走知识库页顶部「补索引」一键批量重跑（§3.3 reindex，embed failed 与模型变更同入口）。

## 8. 配置与依赖（零新增环境变量）

- 复用 `QL_KEY` / `LLM_URL`（chat / embeddings / rerank 同 base）。
- `config.py` 写死常量：`EMBED_MODEL='bge-m3'`、`RERANK_MODEL='bge-reranker-v2-m3'`、`EMBED_BATCH=32`、`RECALL_TOPK=30`、`FUSE_TOPK=20`、`FINAL_TOPK=8`、`RRF_K=60`。
- 依赖仅加 **sqlite-vec**（纯 wheel，pip 装即带各平台扩展二进制，无 C 编译；已 `uv add sqlite-vec` 实测通过）。

## 9. 里程碑（每个独立可交付）

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| M1 写入链路 | 三表建表 + 启动挂扩展 + `_embed` + reindex + 级联删除 + 孤儿恢复 | reindex 后 curl 抽查 chunk 数、vec KNN 与 FTS 命中；删视频后三处同清 |
| M2 检索 | vec KNN + BM25/LIKE + RRF + `/kb/search` + 搜片段 UI | 同义改写句能命中（向量功劳）、2 字专有名词能命中（LIKE 功劳） |
| M3 问答 | `/kb/ask` + 对话面板 + 引用 chip → 弹窗 seek | 多轮追问「它呢？」改写正确；点引用从正确秒数播放 |
| M4 增强 | rerank + scope 过滤完善 | 人工对比 top8 命中率；换 embedding 模型后 reindex 仅重算向量 |

## 10. 风险与对策

| 风险 | 对策 |
| --- | --- |
| trigram 短查询不命中（已实测 2 字为空） | <3 字一律 LIKE 兜底，双路召回天然覆盖 |
| `generate_schemas` 不给存量表加列 | 表结构变更清库重建（项目约定），不做存量迁移 |
| 扩展加载依赖 sqlite3 编译开关 | 已实测本机 stdlib 与 aiosqlite 双路径可加载；docker 用官方 python 基础镜像，同带该开关 |
| vec0 API 未到 1.0 | 锁版本 `sqlite-vec>=0.1.9,<0.2`；建表/KNN/删除全部集中在 `core/vectorstore.py` 单文件，升级只动一处 |
| 量级真打满（百万块） | vec0 现为 SIMD 暴力 KNN：十万块毫秒级、百万开始吃力。升级路径 **pgvector**——config 已预留 postgres 连接，届时 `vectorstore.py` 换实现、`rag.py` 上层接口不变 |
| embeddings 限流/超时 | 批 32 + 批级线性退避重试；失败置 failed 可单独重跑，不伤转写/报告 |
| embedding 模型更换致向量混存 | `model` 列指纹校验，不匹配即整库 reindex（只重算向量，不重转写） |
| SQLite 写并发 | 单 worker 部署不变，索引写仅发生在 `_embed`，与现有下载写同构 |

## 11. 测试

`test.py` 加 `kb_smoke(url)`：提交采集 → 等转写 → `reindex` → `search('视频里的核心观点')` → `ask('这个视频讲了什么？依据哪几段？')`，断言 citations 非空且 start 落在时长内。
