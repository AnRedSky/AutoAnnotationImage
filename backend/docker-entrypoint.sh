#!/bin/bash
# ============================================================
# docker-entrypoint.sh — 容器入口脚本
# ----------------------------------------------------------------
# 解决 Docker 命名卷以 root 挂载导致非 root 用户无法写入的问题
# 1. 以 root 身份创建必要目录并修正属主
# 2. 通过 gosu 降权到 app 用户执行主命令
# ============================================================
set -e

# 需要可写的卷挂载点 (docker-compose volumes)
WRITABLE_DIRS=(
    "/app/models"
    "/app/models/data"
    "/app/models/cache"
    "/app/models/cache/huggingface"
    "/app/uploads"
    "/app/logs"
)

# 1) 创建缺失目录 + 修正属主
for dir in "${WRITABLE_DIRS[@]}"; do
    mkdir -p "$dir" 2>/dev/null || true
    chown -R app:app "$dir" 2>/dev/null || true
done

# 2) 降权执行 (gosu 比 su/sudo 更适合容器环境, 保留信号传递)
if [ "$(id -u)" = "0" ]; then
    exec gosu app "$@"
else
    exec "$@"
fi
