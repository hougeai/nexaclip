# NexaClip 架构与实现说明

音视频**知识库**：粘贴链接或上传文件 → 自动下载 → 句级转写 → AI 报告 + 向量索引 → 跨库检索问答 / 高光标注 / 翻译配音。后端 **FastAPI + yt-dlp/f2/B站 API + FFmpeg + SQLite(sqlite-vec)**，前端 **Vue 3 SPA**（UnoCSS，无组件库）。

> 功能设计决策与演进路线（为什么这么做）见 [`roadmap.md`](./roadmap.md)；RAG 实施设计见 [`rag-design.md`](./rag-design.md)。

## 架构

```
浏览器
  ├── 静态资源 ──────► Cloudflare Pages（Vue SPA）
  ├── REST /api/* ───► 后端容器（FastAPI + 渠道层 + AI 管线）
  └── 播放/下载 ─────► GET /media/{vid}/file?kind=…（手写 206 Range，支持 video seek）
```

全链路自动流水线（每步独立状态机，失败可单独重跑，互不阻塞）：

```
贴链接/上传/订阅枚举
  └─ 采集落库(queued) → 下载(downloading→ready) ─→ ASR 转写(transcript + lang_source 自动检测)
                                                │     ├→ AI 报告(ai_status：大纲+章节+图文)
                                                │     └→ 向量索引(embed_status：chunk→embedding→vec/FTS)
                                                └→ 进度经 5s 批量轮询回前端
知识库：跨视频检索/问答（混合召回→RRF→重排，引用带时间戳直跳播放）
手动增值：高光标注(highlight_status) / 双语字幕(同步 srt) / 配音视频(dub_status 异步)
```

## 核心设计

- **共享库模型（去重的根基）**：`Video.vid` = 平台视频 ID **全局唯一**——一个视频一份文件、一份转写、一份报告、一份向量；用户归属走 `UserVid(user_id, vid)`。N 个用户采同一视频只下载一次；删除按引用计数，最后一个 UserVid 消失才级联清库（文件/切片/派生产物/索引）。
- **渠道分流**（`probe`/`download` 同构接口，共用 `ProbeError`）：
  - `douyin`：f2 + `a_bogus` 签名（yt-dlp 追不上其风控）
  - `bilibili`：**B 站公开 API 直调**（yt-dlp 抓 B 站网页必 412，故独立渠道；支持视频链接/BV 号/b23.tv 短链）
  - `ytdlp`：其余上千站点（探测走 Python API 省进程开销，下载走子进程 CLI 崩溃可隔离、吐进度行算百分比）
  - `upload`：本地上传（sha256 内容 hash 作 vid 天然去重；跳过下载直接进转写下游，覆盖 DRM/私密/失效视频）
- **AI 全走千流云，一把 key**：ASR（asr-pro，句级时间戳）、chat（deepseek-v4.1-flash，报告/高光/翻译/问答生成）、embeddings（bge-m3）、rerank（bge-reranker-v2-m3）、TTS（tts-flash 配音）。零本地重依赖（不装 torch/fastembed）。
- **RAG 检索三段式**：向量（sqlite-vec）+ 关键词（FTS5 BM25）双路召回 → RRF 融合 → Cross-Encoder 重排；`video_chunk` 主表与两张虚表 id/rowid 对齐，embedding 存模型指纹防换模型脏索引。
- **状态同步用批量轮询**（`GET /media/videos?ids=a,b,c`，5s），不用 SSE/WebSocket——分钟级任务长连接性价比低。弹窗内异步任务（高光/配音/索引）由组件自轮询自洽，宿主无需配合。
- **异步管线范式**：转写成功那次 save 一并置下游 `running`（消除轮询竞态窗口）→ `asyncio.create_task` 起后台协程 → 协程内全程不冒泡、失败落 `*_error` 归一化中文文案；重启时 `running` 全部标 `failed`（孤儿恢复），用户可一键重跑。
- **Range 播放**：starlette 0.37 的 FileResponse 不支持 Range，手写 `core/rangeresp.py`（206 单区间分段流）——章节/文案/引用点击 seek 的地基；派生产物（srt/ass/配音视频）同路由 `kind=` 参数 serve。
- **订阅调度**：APScheduler 单实例，每个 `UserSub.check_hour` 注册每日 cron；订阅枚举按平台 ID diff，新视频一轮全进采集链，过载靠下载并发闸 + 配额兜底。
- **鉴权无 cookie**：全接口需登录；access+refresh 双 token 存 localStorage、`token` 头传递，401/419 单飞刷新重试一次。
- **安全与反爬**：`urlguard` 拒私网/纯 IP/非常规端口（SSRF）；YouTube/抖音/B站 cookies 走本地 Netscape 文件（`data/cookies/www.<站点>.com_cookies.txt`，路径由 `YOUTUBE_COOKIES`/`DOUYIN_COOKIES`/`BILIBILI_COOKIES` 指定）；全局 `Semaphore(2)` 下载闸 + 用户配额 + IP 限流；`--workers 1`。
- **JSON 字段用 `fields.JSONField`**：`transcript_segments`/`ai_chapters`/`highlights` 读写直接是 Python list/dict，免手工 dumps/loads。

## 数据模型（models/）

| 表 | 职责 | 关键字段 |
| --- | --- | --- |
| `User` | 用户 | `user_id`(nanoid8) / `email` / `password`(argon2) / 软删 |
| `Video` | 全局媒体库（一视频一行） | 见下 |
| `UserVid` | 用户↔视频归属 | `(user_id, vid)` 唯一 |
| `VideoChunk` | 转写语义块（检索最小单元） | `vid/seq/text/start/end/model`；伴生 `video_chunk_fts`(FTS5) + `vec_chunk`(sqlite-vec) 虚表 |
| `Subscription` | 订阅源（全局共享一行/频道） | `sid/url(唯一)/name/extractor/avatar/…` 账号快照 + `last_check_at/last_error` 全局状态 |
| `UserSub` | 用户↔订阅 + 个人偏好 | `check_hour`(null=不自动) / `enum_limit`(每轮枚举条数) |

`Video` 字段按管线分组：

| 组 | 字段 |
| --- | --- |
| 标识来源 | `vid`(全局唯一) / `source_url` / `title` / `uploader` / `duration` / `thumbnail` / `extractor` / `channel`(douyin·bilibili·ytdlp·upload) / `publish_at` |
| 下载状态 | `status`(queued·downloading·ready·failed) / `percent` / `error_msg` / `started_at` / `finished_at` |
| 产物 | `file_name` / `file_size`（文件按 `store/{vid}.{ext}` 约定定位，不落路径列） |
| 转写 | `transcript` / `transcript_segments`(JSON) / `transcript_error` / `lang_source`(转写后自动检测 zh·en·ja·ko) |
| AI 报告 | `ai_status` / `ai_chapters`(JSON) / `ai_report`(Markdown) / `ai_error` |
| 向量索引 | `embed_status` / `embed_error` |
| 高光 | `highlight_status` / `highlights`(JSON `[{start,end,type,label,reason}]`) / `highlight_error` |
| 配音 | `dub_status` / `dub_error` / `dub_lang` / `dub_voice`（done 时写入=产物元数据） |

派生产物文件（均随视频删除回收）：`store/{vid}_sub.srt`（双语字幕）、`{vid}_ass.ass`（烧录用译文字幕）、`{vid}_dub.mp4`（配音视频）、`store/clips/{vid}/`（高光切片）。

## API 契约

统一响应 `{code, msg, data}`；前缀 `/api/v1`；静态路径声明在 `/{vid}` 之前。

| 域 | 端点 |
| --- | --- |
| 用户 | `POST /base/register·access_token·refresh_token·verifycode·forgot_password·reset_password` / `GET /base/userinfo` |
| 采集 | `POST /media/probe`（贴链接，探测即自动下载）/ `POST /media/upload`（本地上传，hash 去重）/ `GET /media/videos?ids=`（批量轮询）/ `GET /media/{vid}`（详情全量）/ `GET /media/{vid}/file?kind=video\|sub\|ass\|dub`（Range）/ `DELETE /media/{vid}` / `POST /media/{vid}/retranscribe·reanalyze·reembed·meta` |
| 知识库 | `POST /media/kb/search`（混合检索，scope 平台/账号）/ `POST /media/kb/ask`（跨视频问答；`scope.vid` 锁单视频=弹窗问答）/ `GET /media/kb/status` / `POST /media/kb/reindex` |
| 订阅 | `POST /media/subs`（创建=同步探测，失败不落库）/ `GET /media/subs` / `POST /media/subs/{sid}/settings`（定点小时+枚举条数）/ `POST /media/subs/{sid}/check`（立即采集）/ `{sid}/reprobe` / `DELETE {sid}` |
| 高光 | `POST /media/{vid}/highlights`（生成）/ `{vid}/highlights/clip`（导出：merge=拼一个 mp4，否则分条 zip）/ `GET /media/{vid}/clip/{name}` |
| 翻译配音 | `GET /media/translate/langs`（语种+音色表，前端单一真相源）/ `POST /media/{vid}/translate`（同步 srt）/ `POST /media/{vid}/dub`（异步配音视频） |
| 系统 | `GET /system/health·config` |

## 前端（Vue 3 + Pinia + UnoCSS，无组件库）

```
views/
├── LandingView / AuthView / ResetPasswordView
└── console/
    ├── DownloadView      采集：贴链接 / 上传文件 tab，卡片即时上屏转圈
    ├── HistoryView       记录：平台→账号分组导航、分页排序、5s 轮询、详情弹窗宿主
    ├── KnowledgeView     知识库：搜片段（结果卡下钻）+ 问知识库（对话+引用直跳）+ 补索引
    └── SubscriptionsView 订阅：添加/立即采集/定时与条数设置/重探账号
components/
├── VideoDetailModal      详情弹窗：左播放器(Range)+时间轴高光段+操作，右 6 tab
│                         （视频文案·AI 问答·AI 报告·高光片段·双语字幕·AI 配音）
├── MediaCard / StatusStepper / ProgressBar / UrlComposer / ConfirmDialog / Toaster
```

约定：列表接口 `lite=True` 省大字段，详情弹窗另拉全量；弹窗内异步任务（高光/配音/索引）组件自轮询自洽；下载链接经 `downloadHref` 拼 `?token=`（`<a>` 带不了 header）。

## 技术栈与目录

- **后端**：FastAPI + uvicorn + uv；Tortoise ORM（`JSONField` 用其原生类型）；aiosqlite + sqlite-vec + FTS5；APScheduler；yt-dlp（不锁版本、容器启动自升级）+ f2；httpx；loguru。ruff `line-length=120` 单引号。
- **目录**：`backend/app/{main.py, core/(config·init_app·dependency·security·verifycode·idgen·log·exceptions·urlguard·storage·rangeresp·ytdlp·douyin·bilibili·transcript·llm·ai_report·vectorstore·rag·background·highlight·langdetect·translate), models/, schemas/, controllers/video.py, api/v1/(base·media·system), conf/conf.yaml, data/(db.sqlite3·store/·work/·logs/·cookies)}`。
- **前端**：Vue 3 + Vite + Pinia + axios + UnoCSS；`VITE_API_BASE` 空走 Vite proxy。

## 部署

Docker 单容器（非 root、`--workers 1`），端口 `BACK_END_PORT`（默认 7860）；前端 Pages 托管 + `public/_redirects` SPA 回退。关键环境变量：`DB_TYPE`、`JWT_SECRET_KEY`（固定，否则重启 token 失效）、`QL_KEY`（AI 全链路一把 key：chat/embedding/rerank/ASR/TTS）、`SUPER_ADMIN_*`、`USER_FE_URL`、`EMAIL_*`、`SUB_ENUM_LIMIT`、`DOWNLOAD_CONCURRENCY`；cookies 走本地 Netscape 文件（`data/cookies/www.<站点>.com_cookies.txt`，路径可用 `YOUTUBE_COOKIES`/`DOUYIN_COOKIES`/`BILIBILI_COOKIES` 覆盖）；可选 `HTTP_PROXY_URL`。

## 相关文档

- 抖音 SecSDK 签名换盐/排障：[douyin-secsdk.md](douyin-secsdk.md)

## 免责

仅供个人合法用途，遵守目标平台条款与著作权法，勿用于批量抓取或商业分发。
