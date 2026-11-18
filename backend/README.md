# backend

NexaClip 后端：FastAPI + yt-dlp / f2 / B 站 API + FFmpeg + SQLite，成品永久存本地 `data/store/`。

架构、数据模型、API 契约、核心设计与部署详见 [`../docs/architecture.md`](../docs/architecture.md)；RAG 实施设计见 [`../docs/rag-design.md`](../docs/rag-design.md)；功能取舍见 [`../docs/design.md`](../docs/design.md)。

## 开发

```bash
cp app/.env.example app/.env   # 填 QL_KEY（千流云）等
bash app/run.sh                # 本地：后台启动（读 .env、按端口杀旧进程）
bash app/run.sh docker         # 容器：exec 前台运行
# 或前台：cd app && uv run python main.py
```

- 零云依赖起步：`DB_TYPE=sqlite`，首次启动 `generate_schemas(safe=True)` 自动建表。
- 反爬 cookies：浏览器扩展「Get cookies.txt LOCALLY」登录目标站后导出的 Netscape txt，放 `app/data/cookies/www.<站点>.com_cookies.txt`（YouTube / 抖音 / B 站，路径可用 `.env` 的 `*_COOKIES` 覆盖）。
