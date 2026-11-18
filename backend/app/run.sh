#!/bin/bash
# 启动入口：local=本地开发机（后台跑 + 按端口杀旧进程），docker=容器（平台注入环境变量，exec 前台运行便于优雅退出）
set -e
cd "$(dirname "$0")"

MODE=${1:-local}  # local=本地开发机，docker=docker容器

if [ "$MODE" = "local" ]; then
    # 本地开发：读取本地 .env（可能存在），并清理占用端口的旧进程
    set -a; [ -f ./.env ] && . ./.env; set +a
    PORT="${BACK_END_PORT:-7860}"

    PIDS=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | sort -u)
    if [ -n "$PIDS" ]; then
        echo "killing PID(s) on port $PORT: $PIDS"
        kill $PIDS 2>/dev/null || true
        sleep 0.5
        kill -9 $PIDS 2>/dev/null || true
        sleep 0.3
    fi

    nohup uv run python main.py > /dev/null 2>&1 &
    echo "main-app Server started"
else
    # 容器：优先读随镜像带入的 ./.env（与本地开发同构，构建前改好）；无 .env 时环境变量兜底（平台注入模式）
    set -a; [ -f ./.env ] && . ./.env; set +a
    PORT="${BACK_END_PORT:-7860}"
    WORKERS="${UVICORN_WORKERS:-1}"
    # 用 exec 让 uvicorn 成为 PID 1，便于收到 SIGTERM 时优雅退出；
    # --no-sync：镜像构建期已装好依赖，启动不再联网重解析
    exec uv run --no-sync uvicorn main:app --host 0.0.0.0 --port "$PORT" --workers "$WORKERS" --no-access-log
fi
