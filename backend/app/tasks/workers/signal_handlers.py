"""
Worker SIGTERM handler (P0-1, see docs/38-后端架构现状评估与拆分部署方案.md)
================================================================================

问题:
- ``app/tasks/api/training/jobs.py:cancel_training_job`` 已经调用了
  ``celery_app.control.revoke(job_id, terminate=True, signal="SIGTERM")``.
- 但 ``app/tasks/workers/`` 中**没有任何** SIGTERM handler, worker 进程收到
  SIGTERM 后走 Python 默认处理 (抛 SystemExit 后退出), 不写 emergency checkpoint,
  不更新 Redis, 不优雅收尾.

修复:
- 在 ``_worker_main()`` 启动 worker 进程前调用 ``install_sigterm_handler()``
- handler 写 ``train_emergency:{task_id}`` 到 Redis (含 reason + timestamp)
- 不破坏 Ctrl+C (SIGINT) 默认行为
- handler 内部 try/except 全包裹, 写日志但绝不抛 (避免 worker 进程直接退出)

设计说明:
- "获取 active task" 通过 ``_get_active_task_id(celery_app)`` 抽象, 允许测试 mock.
  真实实现: 用 ``celery_app.contrib.abortable`` 没装, 改为简单的全局变量
  ``_CURRENT_TASK_ID`` (long-running 任务入口显式 set, exit clear).
- Redis client 通过 ``_get_redis()`` 获取, 与 ``app/database/redis.py`` 保持一致.
- 仅依赖 stdlib 的 ``signal`` + ``logging``, 不修改 celery 源码.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("app.tasks.workers.signal_handlers")

# 当前 worker 进程内 active 的 task_id.
# long-running 任务入口显式 set, exit 显式 clear.
_CURRENT_TASK_ID: Optional[str] = None
_CURRENT_TASK_LOCK = threading.Lock()


# -----------------------------------------------------------------------------
# Active task 跟踪
# -----------------------------------------------------------------------------

def set_current_task_id(task_id: Optional[str]) -> None:
    """声明 "我现在是这个 task_id" 给 SIGTERM handler 用.

    在 train_model_task / train_detection_task / train_segmentation_task 入口
    调一次. try/finally 释放.
    """
    global _CURRENT_TASK_ID
    with _CURRENT_TASK_LOCK:
        _CURRENT_TASK_ID = task_id


def get_current_task_id() -> Optional[str]:
    """SIGTERM handler 用: 读当前 active task_id."""
    with _CURRENT_TASK_LOCK:
        return _CURRENT_TASK_ID


# 测试期用一个明显标记的 name, 让 patch 容易命中.
_get_active_task_id = get_current_task_id


# -----------------------------------------------------------------------------
# Redis client 获取
# -----------------------------------------------------------------------------

_redis_client_singleton = None
_redis_lock = threading.Lock()


def _get_redis():
    """懒加载 Redis client. 第一次调用时连一次.

    设计: 跟 app.database.redis 模块风格保持一致; 但这里不强依赖
    aioredis-py 的具体版本, 直接走 from settings 拼装.
    """
    global _redis_client_singleton
    if _redis_client_singleton is not None:
        return _redis_client_singleton
    with _redis_lock:
        if _redis_client_singleton is not None:
            return _redis_client_singleton
        try:
            from app.core.config import settings
            import redis as redis_sync
            auth = f":{settings.REDIS_PASSWORD}@" if settings.REDIS_PASSWORD else ""
            url = f"redis://{auth}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
            _redis_client_singleton = redis_sync.from_url(
                url, socket_connect_timeout=1.0, socket_timeout=1.0,
            )
        except Exception as e:
            logger.warning(f"signal_handlers: cannot build redis client ({type(e).__name__}: {e!r})")
            _redis_client_singleton = None
        return _redis_client_singleton


# -----------------------------------------------------------------------------
# SIGTERM handler
# -----------------------------------------------------------------------------

def _build_handler(celery_app: Any) -> Any:
    """返回一个 SIGTERM handler 闭包."""

    def _handler(signum, frame):
        # 全程 try/except - 任何异常都要被捕获, 不能抛出导致 worker 进程 exit.
        try:
            task_id = _get_active_task_id()
            if not task_id:
                logger.warning(
                    "[SIGTERM] received but no active task; "
                    "falling back to default Python handler (exits)"
                )
                return  # 不写 redis; 让默认处理继续

            logger.warning(f"[SIGTERM] active task={task_id}, writing emergency marker to Redis")
            redis_client = _get_redis()
            if redis_client is None:
                logger.warning("[SIGTERM] no redis client; cannot write emergency marker")
                return

            marker_key = f"train_emergency:{task_id}"
            payload = (
                f'{{"reason":"sigterm","timestamp":{int(time.time())},'
                f'"task_id":"{task_id}"}}'
            )
            redis_client.set(marker_key, payload, ex=3600)  # 1 小时 TTL, 足够后续 reconcile
            logger.warning(f"[SIGTERM] wrote {marker_key}={payload}")
        except Exception as exc:  # noqa: BLE001
            # 任何错误都不能让 worker 因为 SIGTERM 立刻 crash. 仅记日志.
            logger.exception(f"[SIGTERM] handler swallowed exception: {exc!r}")

    return _handler


def install_sigterm_handler(celery_app: Any) -> None:
    """装入 SIGTERM handler 到当前进程.

    必须在 worker 启动前 (即 ``celery_app.worker_main()`` 调用前) 装入.
    不覆盖 SIGINT (Ctrl+C) 默认行为 (start_workers.py 的 Ctrl+C 优雅退出依赖此).
    """
    handler = _build_handler(celery_app)
    try:
        prev = signal.signal(signal.SIGTERM, handler)
        logger.info(f"[install_sigterm_handler] installed (previous handler: {prev!r})")
    except (ValueError, OSError) as e:
        # 某些环境 (非主线程 / 容器 init 阶段) 装不上, 记录日志但不让启动失败.
        logger.warning(f"[install_sigterm_handler] failed to install SIGTERM handler: {e!r}")
