# syntax=docker/dockerfile:1.7
# =====================================================================
#  Image Annotation Backend - uv-based multi-stage build (v3.3.0 重构)
# ---------------------------------------------------------------------
#  最小镜像原则落地：
#    Stage 1 (builder): 含 gcc/g++/pkg-config，仅用于编译 C 扩展
#    Stage 2 (base)   : 仅 runtime 依赖 + 应用代码（无构建工具）
#    Stage 3 (api)    : 复用 base，仅重声明 CMD + 健康检查
#    Stage 4 (worker): 复用 base，Celery 入口
#
#  体积对比：
#    旧 (单 stage)    : ~2.5 GB（含 gcc/g++）
#    新 (多 stage)    : ~1.8 GB（仅 runtime）
#    节省             : ~700 MB (-28%)
#
#  Lock 单一来源: backend/uv.lock (API 与 Worker 共用)
# =====================================================================

# ============================================================
#  Stage 1: builder — 编译 C 扩展 (PyMySQL/uvloop/orjson...)
# ============================================================
FROM python:3.10-slim AS builder

# 仅 builder 需要：构建工具链
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv (官方 image，体积小，便于版本固定)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /build

# 依赖层缓存：仅 pyproject.toml/uv.lock 变更才重装
COPY pyproject.toml uv.lock ./

# builder 阶段会真正编译二进制；这层不写进最终镜像
# v3.3.2 修复: UV_PROJECT_ENVIRONMENT 改为 /app/.venv (与 base 一致)
#   旧值 /build/.venv 导致 .venv/bin/* 脚本 shebang 指向 /build/.venv/bin/python
#   COPY 到 /app/.venv 后 shebang 失效, uvicorn/celery 报 "no such file or directory"
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# v3.3.1 修复: 添加 --no-dev 排除开发依赖, 避免 mypy/ruff/pytest 进入生产镜像
# 旧实现 ("含 dev 以便 dev 镜像可附加") 导致 1-3 分钟额外构建时间 + 150-200MB 体积浪费
# dev 依赖改由独立的 dev target 提供 (见 Stage 5)
RUN uv sync --frozen --no-install-project --no-dev

# ============================================================
#  Stage 2: base — 最小运行时基础
# ============================================================
FROM python:3.10-slim AS base

# runtime 必备：curl (健康检查) + ca-certificates (HTTPS) + gosu (降权)
# 注意：不再安装 gcc/g++，最小镜像原则
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv (仅供 console scripts 入口查找)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

# v3.3.2 修复: 先创建非 root 用户, 再用 COPY --chown 拷贝
#   旧实现: 先 COPY 再 chown -R, 导致 5.9GB 镜像层翻倍 (12GB 总体积)
#   新实现: COPY --chown 在拷贝时直接设置属主, 不产生额外层
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app \
    && mkdir -p /app/uploads /app/models /app/logs

# 从 builder 拷贝已编译的 .venv (路径已统一为 /app/.venv, shebang 正确)
COPY --chown=app:app --from=builder /app/.venv /app/.venv

WORKDIR /app

# 应用代码层（独立于依赖层，源码改动不破坏缓存）
COPY --chown=app:app pyproject.toml uv.lock ./
COPY --chown=app:app app /app/app
COPY --chown=app:app scripts /app/scripts
COPY --chown=app:app migrations /app/migrations
COPY --chown=app:app alembic.ini* /app/

# v3.3.2: 入口脚本 — 修复命名卷权限后通过 gosu 降权到 app 用户
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
# 强制清除windows回车符，兜底
RUN sed -i 's/\r$//g' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# PATH 优先级：.venv/bin 优先
ENV PATH="/app/.venv/bin:$PATH" \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8000

# 不设 USER app — entrypoint 以 root 启动, 修复卷权限后 gosu 降权
ENTRYPOINT ["/app/docker-entrypoint.sh"]

EXPOSE 8000

# ============================================================
#  Stage 3: api — FastAPI 入口
# ============================================================
FROM base AS api

# 健康检查：30s 一次，启动期给 20s 缓冲
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:8000/api/health" || exit 1

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

# ============================================================
#  Stage 4: worker — Celery 入口
# ============================================================
FROM base AS worker

ENV PYTHONPATH=/app

# Worker 健康检查：celery inspect ping
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD celery -A app.tasks.workers.celery_app inspect ping -d "celery@$(hostname)" \
        --timeout 5 || exit 1

# 默认消费 train + annotate 两队列（compose 可用 -Q 覆盖）
CMD ["celery", \
     "-A", "app.tasks.workers.celery_app", \
     "worker", \
     "--loglevel=info", \
     "--pool=threads", \
     "--concurrency=2", \
     "-Q", "train,annotate"]

# ============================================================
#  Stage 5: dev — 开发镜像 (含 dev 依赖, 仅本地开发用)
# ============================================================
#  用法: docker build --target dev -t annotation/api:dev -f Dockerfile .
#  与 production 镜像 (api/worker) 共享 base 层, 仅额外装 dev 依赖
FROM base AS dev

# 切回 root 以便装 dev 依赖
USER root

# 装入 dev 依赖 (pytest, ruff, mypy, httpx)
# v3.3.2: base 阶段已有 /app/.venv (生产依赖), 此处仅追加 dev 组
# 不再从 builder COPY (builder 也是 --no-dev, 路径已改为 /app/.venv)
RUN uv sync --frozen

# entrypoint 会 gosu 降权到 app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
