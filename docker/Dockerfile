# NexaClip 后端镜像：FastAPI + yt-dlp + FFmpeg
# 目标平台：Hugging Face Spaces（sdk: docker、端口 7860、强制非 root uid 1000）
# 构建（context 为仓库根目录）：docker build -f docker/Dockerfile -t nexaclip-backend .
FROM python:3.12-slim

# FFmpeg：音视频流 remux 合并（只混流转封装，不转码）
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# uv：构建期装依赖；运行期 run.sh 的 uv run 也用同一个
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# HF Spaces 要求非 root（uid 1000）运行
RUN useradd -m -u 1000 nexaclip

WORKDIR /app
# 先只拷 pyproject 装依赖：改业务代码不触发依赖层重建
COPY backend/pyproject.toml .
RUN uv venv /app/.venv && \
    uv pip install --no-cache-dir -r pyproject.toml --python /app/.venv/bin/python

# 应用源码平铺进 /app（run.sh 约定 cd 到脚本目录起 uvicorn main:app）
COPY backend/app/. .
# 绝不允许把本地 .env（真凭据）带进镜像；且 run.sh 会 source .env，残留会覆盖平台注入的环境变量
RUN rm -f ./.env && chmod +x run.sh

# 启动入口：先自更新 yt-dlp 再拉起服务（见 entrypoint.sh）
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R nexaclip:nexaclip /app

USER nexaclip
ENV HOME=/app \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

EXPOSE 7860
ENTRYPOINT ["/entrypoint.sh"]
