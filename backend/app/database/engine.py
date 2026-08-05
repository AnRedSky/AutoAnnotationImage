"""
Database Engine (Database Layer)
================================

创建 async SQLAlchemy engine + session factory.

v3.0.0 迁移: 从 app.database 拆出 (Phase 1.9)

v3.5.1 修复: 多线程跨 event loop 错误
======================================
**问题**: 取消任务时报 `RuntimeError: got Future ... attached to a different loop`.
  根因: aiomysql connection 在 thread A 的 event loop 中创建, 但 thread B 借到
  此 connection 后, 在 thread B 的 loop 中执行 ping (pool_pre_ping=True) 触发
  `asyncio.Future` 跨 loop 错误.

**根因深层**:
  - `engine` 是 process-level 单例, 内部连接池绑定到**首个访问的 thread** 的 event loop
  - Celery threads 池 concurrency=4, 4 个 thread 共享同一 engine
  - thread A 跑过任务, 留下 connection 绑在 thread A 的 loop
  - thread B 借到 connection, 在 thread B 的 loop 中 ping → 跨 loop 报错

**修复方案**: thread-local 懒加载 engine
  - 每个 thread 第一次借连接时, lazy 创建该 thread 专属的 engine
  - 不同 thread 的 connection 互不干扰, 不存在跨 loop
  - 主线程 (FastAPI / sync code) 仍然用 process-level engine (沿用旧行为)
  - `engine` / `AsyncSessionLocal` 名称保留, 向后兼容

性能开销:
  - 首次创建 engine ~200ms (TCP 握手 + aiomysql 初始化)
  - 后续借连接 O(1) (本地 thread-local dict 查询)
  - Celery worker 启动慢 200ms, 但训练任务吞吐无影响
"""
import logging
import threading
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker, AsyncEngine

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_engine_kwargs() -> dict:
    """根据数据库类型返回合适的 engine 参数。
    - MySQL: 使用连接池（pool_size / max_overflow）
    - SQLite (aiosqlite): 使用 StaticPool，禁用连接池参数
    """
    url = settings.EFFECTIVE_DATABASE_URL
    if "sqlite" in url:
        # SQLite 内存模式必须 StaticPool，否则多连接看不到表
        from sqlalchemy.pool import StaticPool
        return {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        # 使用前先 ping: MySQL 长连接被服务端静默断开后, 首次请求才报错,
        # pool_pre_ping 让 SQLAlchemy 在借出连接前做一次轻量检测, 失败则重建
        "pool_pre_ping": True,
        # 主动回收: 避免 MySQL wait_timeout(默认 8h) 静默断连
        "pool_recycle": settings.DB_POOL_RECYCLE,
    }


# ============== 主线程 engine (FastAPI / sync code 沿用旧行为) ==============
# 创建于 import 时所在 thread (一般是主线程 / FastAPI 启动时)
# FastAPI 是 async-first, 全部在主 loop 中跑, 不存在跨 loop
# 这就是为什么 FastAPI endpoint 一直没问题, 只有 Celery worker 跨 thread 才有问题

_main_thread_ident = threading.get_ident()
_main_engine: AsyncEngine = create_async_engine(
    settings.EFFECTIVE_DATABASE_URL,
    echo=settings.APP_DEBUG,
    **_build_engine_kwargs(),
)
_main_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    _main_engine, class_=AsyncSession, expire_on_commit=False
)


# ============== thread-local engine (Celery worker 线程用) ==============
# 用 threading.local 隔离每个 thread 的 engine, 避免 aiomysql 跨 loop 错误
_engine_lock = threading.Lock()
_thread_engines: dict[int, AsyncEngine] = {}        # thread_ident -> engine
_thread_session_factories: dict[int, async_sessionmaker[AsyncSession]] = {}
_thread_engine_init_locks: dict[int, threading.Lock] = {}  # 避免同一 thread 内并发初始化


def _build_thread_engine_kwargs() -> dict:
    """thread-local engine 专属参数
    - 关闭 pool_pre_ping: 跨 thread 借连接时不需要 ping 旧 connection
      (因为 engine 已经是 thread-local, 同一 thread 的 loop 不变, ping 同 loop 没问题)
      保留 pool_pre_ping 也 OK, 但保留可减少一次 ping RTT.
    - pool_size 调小: thread-local engine 每个 thread 一个, 4 thread 实际
      占用 4 * pool_size 个连接, 主 pool_size=10 可能会爆 MySQL max_connections
    """
    base = _build_engine_kwargs()
    # 调小 pool_size 防止总连接数超 MySQL 上限 (4 thread * 5 = 20 connections)
    if "pool_size" in base:
        base["pool_size"] = max(1, min(base["pool_size"], 5))
    if "max_overflow" in base:
        base["max_overflow"] = max(0, min(base["max_overflow"], 3))
    return base


def get_thread_engine() -> AsyncEngine:
    """获取当前 thread 专属的 async engine (懒创建)

    - 主线程: 返回 _main_engine (复用, 无创建开销)
    - 其他 thread: 第一次调用时创建该 thread 专属 engine

    用法:
        from app.database.engine import get_thread_engine
        engine = get_thread_engine()  # thread-local
    """
    tid = threading.get_ident()
    if tid == _main_thread_ident:
        return _main_engine
    # thread-local cache hit
    engine = _thread_engines.get(tid)
    if engine is not None:
        return engine
    # 懒创建 (双 check 避免重复创建)
    with _engine_lock:
        init_lock = _thread_engine_init_locks.get(tid)
        if init_lock is None:
            init_lock = threading.Lock()
            _thread_engine_init_locks[tid] = init_lock
    with init_lock:
        engine = _thread_engines.get(tid)
        if engine is not None:
            return engine
        kwargs = _build_thread_engine_kwargs()
        # SQLite 测试场景: 同一 engine 实例, 避免 :memory: 数据库丢失
        if "sqlite" in settings.EFFECTIVE_DATABASE_URL and "memory" in settings.EFFECTIVE_DATABASE_URL:
            # 测试场景下 :memory: 共享一个 engine (主线程创建那个), 多 thread 也用它
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
        engine = create_async_engine(
            settings.EFFECTIVE_DATABASE_URL,
            echo=settings.APP_DEBUG,
            **kwargs,
        )
        # 给 thread-local engine 也绑定慢 SQL 监控 (主 engine 已在 module 加载时绑定)
        try:
            from app.database.slow_sql import setup_slow_sql_monitor
            setup_slow_sql_monitor(engine)
        except Exception:  # noqa: BLE001
            pass
        _thread_engines[tid] = engine
        logger.info(
            "[engine] 创建 thread-local engine (tid=%s, url=%s, pool_size=%s)",
            tid, settings.EFFECTIVE_DATABASE_URL.split("@")[-1], kwargs.get("pool_size"),
        )
        return engine


def get_thread_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取当前 thread 专属的 AsyncSessionLocal (懒创建)"""
    tid = threading.get_ident()
    if tid == _main_thread_ident:
        return _main_session_factory
    factory = _thread_session_factories.get(tid)
    if factory is not None:
        return factory
    with _engine_lock:
        init_lock = _thread_engine_init_locks.get(tid)
        if init_lock is None:
            init_lock = threading.Lock()
            _thread_engine_init_locks[tid] = init_lock
    with init_lock:
        factory = _thread_session_factories.get(tid)
        if factory is not None:
            return factory
        engine = get_thread_engine()
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        _thread_session_factories[tid] = factory
        return factory


async def dispose_thread_engine(tid: Optional[int] = None) -> None:
    """释放指定 thread (默认当前 thread) 的 engine + connection pool

    用途:
    1. Worker 线程优雅退出 (start_workers.py shutdown hook)
    2. 测试场景: 清理 thread-local engine 避免 fixture 切换 engine 后脏连接

    Args:
        tid: 要释放的 thread ident. 默认当前 thread.
    """
    if tid is None:
        tid = threading.get_ident()
    with _engine_lock:
        engine = _thread_engines.pop(tid, None)
        _thread_session_factories.pop(tid, None)
        _thread_engine_init_locks.pop(tid, None)
    if engine is not None:
        try:
            await engine.dispose()
            logger.info("[engine] dispose thread-local engine (tid=%s)", tid)
        except Exception as e:
            logger.warning("[engine] dispose thread-local engine failed (tid=%s): %s", tid, e)


# ============== 向后兼容: process-level 别名 ==============
# 保留旧名称, 指向主线程 engine / session factory
# - FastAPI endpoint (Depends(get_db)) 走的是 get_db → AsyncSessionLocal() → 主线程 session factory
# - Celery worker 调 _run_async 时, 已经在 worker 线程中, 但代码里 import 的 AsyncSessionLocal
#   仍然是主线程的, 所以 worker 调 `async with AsyncSessionLocal() as db:` 会出问题

# 解决方案: 把 `AsyncSessionLocal` 和 `engine` 重写为"根据当前 thread 返回对应版本"
# 用 __getattr__ (module-level) 实现动态属性


class _ThreadAwareAlias:
    """thread-aware proxy: 访问属性时按当前 thread 返回对应对象

    实现 engine / AsyncSessionLocal 的"看起来是单例, 实际是 thread-local" 的能力
    - 旧调用方 `async with AsyncSessionLocal() as db:` 仍可工作
    - 内部按当前 thread 选择 thread-local 或主线程版本
    """
    __slots__ = ("_resolve",)

    def __init__(self, resolve):
        self._resolve = resolve

    def __call__(self, *args, **kwargs):
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._resolve(), name)


def _resolve_engine():
    return get_thread_engine()


def _resolve_session_factory():
    return get_thread_session_factory()


# 暴露 thread-aware 别名
# - 旧代码: `from app.database import engine` → 拿到 thread-aware proxy
# - 旧代码: `async with AsyncSessionLocal() as db:` → proxy() → thread-local session factory
# - 新代码: `from app.database.engine import get_thread_engine` → 直接拿
engine = _ThreadAwareAlias(_resolve_engine)
AsyncSessionLocal = _ThreadAwareAlias(_resolve_session_factory)


# Stage 5.4: 绑定慢 SQL 监控 (event listener)
# 只在主 engine 上绑定 (thread-local engine 同样在创建时绑定)
try:
    from app.database.slow_sql import setup_slow_sql_monitor
    setup_slow_sql_monitor(_main_engine)
except Exception as e:  # noqa: BLE001
    logger.warning("setup_slow_sql_monitor failed: %r", e)


# Re-export for convenience
__all__ = [
    "engine",
    "AsyncSessionLocal",
    "get_thread_engine",
    "get_thread_session_factory",
    "dispose_thread_engine",
]

