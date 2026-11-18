# NexaClip 部署

前后端彻底分离：**后端 → Docker 容器**，**前端 → 任意静态托管**（Cloudflare Pages / Netlify / Nginx 均可），成品永久存本地 `data/store/`。

```
docker/
├── Dockerfile          # 后端镜像（python:3.12-slim + ffmpeg + uv，非 root uid 1000，端口 7860）
├── entrypoint.sh       # 启动前自更新 yt-dlp（NEXACLIP_NO_UPDATE=1 跳过）
├── docker-compose.yml  # 本地联调同一镜像
└── README.md           # 本文件
```

## 本地构建 / 运行

```bash
# 仓库根目录执行（context 必须是根，Dockerfile 里 COPY backend/...）
docker build -f docker/Dockerfile -t nexaclip-backend .
docker run -p 7860:7860 -v ./docker/data:/app/data nexaclip-backend

# 或用 compose（等价，含 extra_hosts）
docker compose -f docker/docker-compose.yml up --build
```

镜像内**不带 .env**（凭据绝不入镜像）：配置全部走环境变量，用 `docker run --env-file backend/app/.env` 注入。

## 后端：Docker 部署

任意支持 Docker 的主机或容器平台：

1. 以本仓库根目录为构建上下文构建 `docker/Dockerfile`
2. 挂载一个持久卷到 `/app/data`（SQLite、成品、日志都在这里）
3. 用环境变量注入配置（见下表）；端口默认 7860，可用 `BACK_END_PORT` 修改

### 环境变量

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `JWT_SECRET_KEY` | ✅ | 随机长串且**固定**，留默认值会令每次重启所有 token 失效 |
| `DB_TYPE` | ✅ | `sqlite`（默认）；转 `postgres` 需补 `PG_*` 五项 |
| `USER_FE_URL` | ✅ | 前端公网地址（重置密码邮件链接前缀） |
| `QL_KEY` | ✅ | 千流云 key（chat / embedding / rerank / ASR / TTS 共用一把） |
| `SUPER_ADMIN_EMAIL/NAME/PASSWORD` | 建议 | 首启自动建管理员，留空则不建 |
| `EMAIL_HOST/PORT/USER/PASSWORD/SENDER` | 建议 | 注册验证码 / 找回密码，不配则相关功能不可用 |
| `YOUTUBE_COOKIES` / `DOUYIN_COOKIES` / `BILIBILI_COOKIES` | 可选 | cookies 路径（默认 `data/cookies/www.<站点>.com_cookies.txt`）；浏览器插件导出 Netscape txt 放到位即可。抖音需 `backend/app/conf/conf.yaml` 的浏览器指纹与导出浏览器一致，否则 403 |
| `HTTP_PROXY_URL` | 可选 | 数据中心 IP 触发 YouTube bot 校验时配 |

## 前端：静态托管

纯静态，不需要任何容器：

- **Build command**：`pnpm install && pnpm run build`
- **Build output directory**：`dist`
- **环境变量**：`VITE_API_BASE=https://api.example.com/api/v1`（填你自己的后端地址，末尾带 `/api/v1`；留空则走同域相对路径，需反代 `/api` 到后端）
- **SPA 路由回退**：已内置 `frontend/public/_redirects`（`/* /index.html 200`），随 dist 发布；用 Nginx 时等价配置为 `try_files $uri $uri/ /index.html`。缺了它，线上刷新子路由会 404

## 数据目录挂载

`entrypoint.sh` 启动时自动检测：若平台把持久卷挂到了 `/data`，而 `/app/data` 不是软链，就先合并 `/app/data` 的内容到 `/data`，再 `ln -s /data /app/data`。没有 `/data` 时此段自动跳过，走你挂载的 `./data` 卷。

## 常见坑

- **yt-dlp 更新失败**：entrypoint 里失败不阻断启动（沿用镜像内版本），日志有 `[entrypoint]` 提示。
- **反向代理**：前端跨域直连后端时，后端已放开 CORS；若同域部署，反代需转发 `/api` 并透传 `Range` / `Content-Range`（视频拖动依赖）。
