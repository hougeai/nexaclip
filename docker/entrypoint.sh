#!/bin/bash
# 容器入口：启动前自更新 yt-dlp（extractor 失效靠重启 Space 当天修复；NEXACLIP_NO_UPDATE=1 跳过），
# 然后 exec run.sh docker 让 uvicorn 接管 PID 1，收 SIGTERM 优雅退出。
set -e

# ---- 数据目录：Space 挂载 /data 时软链到 /app/data，成品/SQLite/日志持久化到 /data ----
# 本地联调不挂 /data，走 docker-compose 的 ./data 卷，此段自动跳过。
if [ -d /data ] && [ ! -L /app/data ]; then
  if [ -d /app/data ] && [ -n "$(ls -A /app/data 2>/dev/null)" ]; then
    # /app/data 已有内容（旧卷残留）：合并搬走，避免覆盖
    cp -a /app/data/. /data/
    rm -rf /app/data
  fi
  ln -s /data /app/data
fi

if [ "${NEXACLIP_NO_UPDATE:-0}" != "1" ]; then
    uv pip install -U --no-cache-dir yt-dlp --python "$VIRTUAL_ENV/bin/python" -q \
        || echo '[entrypoint] yt-dlp 更新失败，沿用镜像内版本'
fi
exec /app/run.sh docker