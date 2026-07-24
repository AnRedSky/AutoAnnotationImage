"""
异步桥 Helpers (Utils Layer)
============================

提供 Celery 任务中运行 async 代码的工具, 以及其他通用 async helpers.

v3.0.0 迁移: 从 app.core.celery_utils 迁入 app.utils.async_helpers
"""
import asyncio
import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def check_celery_available() -> None:
    """检测 Redis 是否可达. 不可达则 503, 避免任务在 .delay() 处长时间阻塞."""
    from app.core.redis_client import redis_client  # noqa: PLC0415
    try:
        redis_client.ping()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Redis 不可用, 任务无法入队: {e}")


def run_async_in_worker(coro):
    """
    在 fresh event loop 中跑 coroutine, 并 dispose engine 防止
    SQLAlchemy async 连接池绑到已关闭的 loop 上。

    背景: Celery 任务是 sync 函数, 内部用 asyncio 调 async DB 代码。
    多次创建/关闭 event loop 时, SQLAlchemy async engine 的连接池会缓存 loop
    引用, 第二次调用会出现 "Event loop is closed" /
    "NoneType has no attribute send" (greenlet bridge 失败)。
    每次 dispose engine 强制清空 pool, 重建时绑到新 loop 上。
    """
    from app.database import engine  # noqa: PLC0415

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        # 清理旧 loop 残留的连接
        try:
            loop.run_until_complete(engine.dispose())
        except Exception:  # noqa: BLE001
            logger.debug("pre-run engine.dispose() skipped", exc_info=True)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        except Exception:  # noqa: BLE001
            logger.debug("post-run engine.dispose() skipped", exc_info=True)
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass
