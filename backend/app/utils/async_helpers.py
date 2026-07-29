"""
异步桥 Helpers (Utils Layer)
============================

提供 Celery 任务中运行 async 代码的工具, 以及其他通用 async helpers.

v3.0.0 迁移: 从 app.core.celery_utils 迁入 app.utils.async_helpers

v3.1.0 性能优化: run_async_in_worker 改为线程局部单例 loop, 不再每次 dispose engine.
旧实现每次调用都 new_event_loop + engine.dispose×2, 单任务产生 50-100 次 TCP 重连.
新实现 loop 生命周期 = 线程生命周期, 连接池绑定到复用的 loop, 零额外重连.
"""
import asyncio
import logging
import threading
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# 线程局部存储: 每个线程 (Celery worker 线程池下) 独立 event loop
# threads 池 concurrency=N → N 个线程各持有 1 个 loop, 互不干扰
_thread_local = threading.local()


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    """获取当前线程的持久 event loop, 不存在则创建.

    线程局部单例 loop:
    - 首次调用: new_event_loop + set_event_loop, 返回新 loop
    - 后续调用: 复用已有 loop (is_closed() 时重建)
    - loop 生命周期 = 线程生命周期, 连接池绑定到该 loop
    """
    loop = getattr(_thread_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _thread_local.loop = loop
    else:
        asyncio.set_event_loop(loop)
    return loop


def check_celery_available() -> None:
    """检测 Redis 是否可达. 不可达则 503, 避免任务在 .delay() 处长时间阻塞."""
    from app.database.redis import redis_client  # noqa: PLC0415
    try:
        redis_client.ping()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Redis 不可用, 任务无法入队: {e}")


def run_async_in_worker(coro):
    """
    在线程局部单例 event loop 中跑 coroutine.

    v3.1.0 性能优化 (Phase W1.1):
    - 旧实现: 每次调用 new_event_loop + engine.dispose×2 + close
      → 单任务 50-100 次 TCP 重连 (每次 dispose 丢弃连接池所有连接)
    - 新实现: 线程局部单例 loop, 连接池绑定到复用的 loop
      → 零额外重连, 连接池 warm hit

    背景: Celery 任务是 sync 函数, 内部用 asyncio 调 async DB 代码。
    旧实现每次新建/关闭 loop 时, SQLAlchemy async engine 的连接池会缓存
    旧 loop 引用导致 "Event loop is closed"。新实现用线程局部单例 loop
    彻底消除此问题 — loop 不关闭, 连接池始终有效。

    线程安全: threads 池下每个线程独立 loop (threading.local), 互不干扰。
    solo 池下只有一个线程, 单 loop 贯穿整个 worker 生命周期。
    """
    loop = _get_worker_loop()
    return loop.run_until_complete(coro)


def dispose_worker_loop() -> None:
    """显式释放当前线程的 event loop + engine 连接池.

    仅在 worker 优雅关闭时调用 (start_workers.py 的 shutdown hook).
    正常任务执行期间不应调用 — 会让后续 DB 查询重建连接。
    """
    global _thread_local
    loop = getattr(_thread_local, "loop", None)
    if loop is None or loop.is_closed():
        return
    try:
        from app.database import engine  # noqa: PLC0415
        loop.run_until_complete(engine.dispose())
    except Exception:  # noqa: BLE001
        logger.debug("dispose_worker_loop: engine.dispose skipped", exc_info=True)
    try:
        loop.close()
    except Exception:  # noqa: BLE001
        pass
    _thread_local.loop = None
