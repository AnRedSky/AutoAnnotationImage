"""
Celery 工具函数（API + Worker 共享）
====================================
收敛两处重复逻辑:
- ``check_celery_available``: 之前在 api/detection.py 与 api/segmentation.py 各写一份
- ``run_async_in_worker``: 之前在 workers/tasks.py / detection_tasks.py /
  segmentation_tasks.py / ml/train.py 各写一份 (~15 行 × 4)

统一在此维护, 保证行为一致, 一处修改全局生效。
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
