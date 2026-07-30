# syntax=docker/dockerfile:1.7
# =====================================================================
#  Image Annotation Backend - uv-based multi-stage build
# ---------------------------------------------------------------------
#  - Base image    : python:3.10-slim
#  - Package mgr   : uv (https://docs.astral.sh/uv/)
#  - Lock source   : backend/uv.lock (单一来源, API 与 Worker 共用)
#  - 端到端缓存    : UV_LINK_MODE=copy 兼容 Windows host bind-mount
#                    不再有 Linux-only hardlink 失败问题
# =====================================================================

FROM python:3.10-slim AS base

# System deps（按需：gcc/g++ 给 PyMySQL 与潜在 C 扩展，libpq 备用）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ pkg-config curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv (官方 image，体积小，便于版本固定)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# ---- 依赖层缓存 ----
# 利用 Docker layer cache：仅 pyproject.toml/uv.lock 变更才重装依赖。
# 复制到独立路径避免根目录其他文件变更误伤缓存键。
COPY pyproject.toml uv.lock ./

# UV_LINK_MODE=copy: 避免 Linux bind-mount symlink 跨设备失败
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# --no-install-project：只装运行时依赖，不安装项目本身；
# 这样 source 改动不影响依赖层（Docker 缓存键稳定）。
# extras：dev 在生产构建中不装，dev 镜像可显式加 --extra dev。
RUN uv sync --frozen --no-install-project --no-dev

# ---- 应用代码层 ----
# 把整个 backend 拷贝进去。当前 backend/ 不大，按"全量 COPY"足够清晰；
# 如果进一步变大可以拆 "COPY app /app/app" 进一步分层。
COPY . /app

# 把项目本体以非 editable 方式装进 .venv (确保 console scripts 可用)
RUN uv sync --frozen --no-dev --no-editable

# 创建统一入口可执行 wrapper (uv-installed console scripts 在 .venv/bin)
ENV PATH="/app/.venv/bin:$PATH"

# default uid/gid 调整为可写 uploads/models
RUN groupadd -r app && useradd -r -g app -d /app app \
    && mkdir -p /app/uploads /app/models /app/logs \
    && chown -R app:app /app
USER app

EXPOSE 8000

# 健康检查 (API 容器使用，worker 容器可在 compose 中覆盖)
HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT:-8000}/api/health" || exit 1

# 默认入口：uvicorn。该 stage 单独作为 API 镜像时使用。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
