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

v3.5.2 修复: thread-local engine 缓存命中 stale 引擎
==================================================
**问题**: v3.5.1 用 `threading.get_ident()` (OS tid) 作 dict key, 但 Celery
  `threads` 池 + `worker_max_tasks_per_child=10` 会重启 worker 线程.
  OS 复用 tid 给新线程, 但 Python 给的是全新 `Thread` 对象.
  新线程第一次 DB 调用命中老缓存, 拿到绑在旧线程已关闭 loop 上的 stale engine
  → `await db.execute()` 触发 asyncio 跨 loop 保护 → 抛错.

**修复方案**: 合并 3 个并行 dict 为 `_thread_entries: dict[int, _ThreadEngineEntry]`,
  每条 entry 绑定 (engine, session_factory, 创建线程, loop). 每次
  `get_thread_engine()` 前做 3 条 stale 规则校验:
    1. `entry.thread is threading.current_thread()`  — 抓 OS tid 复用
    2. `entry.thread.is_alive()`                    — 防御性
    3. `entry.loop is current_loop and not closed()` — 抓 loop 替换/关闭
  不满足任一则驱逐旧 entry 并重建. 驱逐用 `engine.sync_engine.dispose()` (同步),
  不依赖运行中的 loop.

性能开销:
  - 首次创建 engine ~200ms (TCP 握手 + aiomysql 初始化)
  - 后续借连接 O(1) (本地 thread-local dict 查询)
  - Stale 校验 ~100ns (2 个 `is` + 1 个 `is_alive()`)
  - Celery worker 启动慢 200ms, 但训练任务吞吐无影响
"""
import asyncio
import logging
import threading
from dataclasses import dataclass
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
# v3.5.2: 合并 3 个并行 dict 为单一 _thread_entries, 每条 entry 绑定
# (engine, session_factory, 创建线程, loop) 作为原子单元, 缓存命中前
# 做 stale 校验. Celery threads 池 worker_max_tasks_per_child 重启后
# OS 复用 tid, 老 entry 必须被驱逐并重建, 否则会拿到绑到 closed loop
# 的 stale engine 触发跨 loop 错误.
#
# 锁策略:
# - `_entries_lock` 保护写入 (创建/驱逐), 读路径 lock-free
# - 同一 thread 内单步完成 entry 创建, 不需要 per-tid init_lock
#   (GIL 保证单 thread 内代码原子性; 不同 thread 间靠 _entries_lock 串行化)

_entries_lock = threading.Lock()
_thread_entries: dict[int, "_ThreadEngineEntry"] = {}


@dataclass
class _ThreadEngineEntry:
    """Per-thread engine 缓存条目 (v3.5.2).

    Attributes:
        engine: 该 thread 专属的 AsyncEngine (含 connection pool)
        session_factory: 对应的 async_sessionmaker (复用 engine)
        thread: 创建该 entry 时的 threading.Thread 对象.
            强引用 (非 weakref), 理由:
              - Celery thread 数受并发数限制 (默认 2), 内存可忽略
              - 强引用让 `is_alive()` 校验更稳
              - 防止 Thread 被 GC 后 weakref 校验误报
        loop: 创建时绑定的 event loop. 用于 stale 校验的规则 3
            (entry.loop 关闭或被替换时判定 stale). None 表示未经过
            `_get_worker_loop` priming (例如: 直接 test 调用).
    """
    engine: AsyncEngine
    session_factory: async_sessionmaker
    thread: threading.Thread
    loop: Optional[asyncio.AbstractEventLoop] = None


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


def _try_get_current_loop() -> Optional[asyncio.AbstractEventLoop]:
    """懒导入 async_helpers._thread_local.loop, 避免循环引用.

    Returns:
        当前 thread 的 event loop (来自 app.utils.async_helpers 的
        thread-local), 若模块未导入或 thread 未经过 `_get_worker_loop`
        priming 则返回 None. 校验失败/异常一律吞掉 (entry.loop is None
        时规则 3 不触发, 不会误报 stale).
    """
    try:
        from app.utils import async_helpers  # noqa: PLC0415
        return getattr(async_helpers._thread_local, "loop", None)
    except Exception:  # noqa: BLE001
        return None


def _is_entry_stale(entry: "_ThreadEngineEntry") -> bool:
    """判定缓存条目是否 stale (v3.5.2).

    3 条规则任一不满足即视为 stale:
        1. `entry.thread is threading.current_thread()` — 抓 OS tid 复用
            (Celery worker 线程被重启后, 新的 Thread 对象与缓存中的不同)
        2. `entry.thread.is_alive()` — 防御性, 防止异常情况下命中死线程
        3. 若 entry.loop 不为 None, 与当前 thread 的 loop 比对
            (entry.loop 关闭或被替换时判定 stale, 兜底 dispose_worker_loop 路径)

    O(1) 开销: 2 个 `is` 比较 + 1 个 `is_alive()` 调用. lazy loop 查找
    失败时规则 3 不触发, 不会误报 stale.
    """
    current = threading.current_thread()
    # 规则 1: 线程身份 (抓 OS tid 复用)
    if entry.thread is not current:
        return True
    # 规则 2: 线程存活 (防御性)
    if not entry.thread.is_alive():
        return True
    # 规则 3: loop 比对 (仅当 entry 有 loop 时)
    if entry.loop is not None:
        cur_loop = _try_get_current_loop()
        if cur_loop is not None:
            if cur_loop is not entry.loop:
                return True
            if cur_loop.is_closed():
                return True
    return False


def _evict_stale_entry_locked(tid: int) -> None:
    """驱逐 + 同步 dispose stale entry (调用方必须持 _entries_lock).

    用 `engine.sync_engine.dispose()` (同步) 而非 `await engine.dispose()`,
    理由: 检测 stale 的调用方 (Celery worker 线程) 此时可能没有运行中的
    event loop, 同步 dispose 关闭连接池即可, 不依赖 loop.
    """
    entry = _thread_entries.pop(tid, None)
    if entry is None:
        return
    try:
        # 同步 dispose: 关闭连接池, 释放连接资源
        entry.engine.sync_engine.dispose()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[engine] stale entry dispose failed (tid=%s): %s", tid, e,
        )
    logger.warning(
        "[engine] evicted stale thread-local engine "
        "(tid=%s, old=%r, new=%r)",
        tid, entry.thread.name, threading.current_thread().name,
    )


def _create_thread_engine() -> AsyncEngine:
    """构造 thread-local AsyncEngine, 抽离原 get_thread_engine 内部逻辑.

    与 v3.5.1 实现完全一致:
      - MySQL: pool_size 上限 5, max_overflow 上限 3
      - SQLite :memory: 走 StaticPool (避免 :memory: 数据库丢失)
      - 创建后绑定慢 SQL 监控
    """
    kwargs = _build_thread_engine_kwargs()
    # SQLite 测试场景: 同一 engine 实例, 避免 :memory: 数据库丢失
    if "sqlite" in settings.EFFECTIVE_DATABASE_URL and "memory" in settings.EFFECTIVE_DATABASE_URL:
        from sqlalchemy.pool import StaticPool  # noqa: PLC0415
        kwargs["poolclass"] = StaticPool
    engine = create_async_engine(
        settings.EFFECTIVE_DATABASE_URL,
        echo=settings.APP_DEBUG,
        **kwargs,
    )
    # 给 thread-local engine 也绑定慢 SQL 监控 (主 engine 已在 module 加载时绑定)
    try:
        from app.database.slow_sql import setup_slow_sql_monitor  # noqa: PLC0415
        setup_slow_sql_monitor(engine)
    except Exception:  # noqa: BLE001
        pass
    return engine


def get_thread_engine() -> AsyncEngine:
    """获取当前 thread 专属的 async engine (v3.5.2 懒创建 + stale 检测).

    - 主线程: 始终返回 ``_main_engine`` (FastAPI / sync code 走主 loop).
    - 其他 thread: 第一次调用时懒创建; 后续命中前做 stale 检测.
      Celery threads 池在 ``worker_max_tasks_per_child=10`` 后会重启
      worker 线程, OS 复用 tid 但新 Thread 是不同对象 — 老 entry 必须
      被驱逐并重建, 否则会拿到绑定到 closed loop 的 stale engine.

    用法:
        from app.database.engine import get_thread_engine
        engine = get_thread_engine()  # thread-local
    """
    tid = threading.get_ident()
    if tid == _main_thread_ident:
        return _main_engine

    # ---- Fast path: lock-free 读 + stale 检测 ----
    entry = _thread_entries.get(tid)
    if entry is not None:
        if not _is_entry_stale(entry):
            return entry.engine
        # Stale: 持锁驱逐
        with _entries_lock:
            _evict_stale_entry_locked(tid)

    # ---- Slow path: 持锁创建 ----
    with _entries_lock:
        # 双 check: 持锁后可能其他线程已创建
        entry = _thread_entries.get(tid)
        if entry is not None and not _is_entry_stale(entry):
            return entry.engine
        if entry is not None:
            _evict_stale_entry_locked(tid)

        engine = _create_thread_engine()
        factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False,
        )
        entry = _ThreadEngineEntry(
            engine=engine,
            session_factory=factory,
            thread=threading.current_thread(),
            loop=_try_get_current_loop(),
        )
        _thread_entries[tid] = entry
        logger.info(
            "[engine] created thread-local engine "
            "(tid=%s, url=%s, pool_size=%s)",
            tid,
            settings.EFFECTIVE_DATABASE_URL.split("@")[-1],
            _build_thread_engine_kwargs().get("pool_size"),
        )
        return engine


def get_thread_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取当前 thread 专属的 AsyncSessionLocal (v3.5.2 懒创建 + stale 检测).

    - 主线程: 始终返回 ``_main_session_factory``.
    - 其他 thread: 走 ``get_thread_engine()`` 拿到 (或重建) entry,
      再返回 entry.session_factory.
    """
    tid = threading.get_ident()
    if tid == _main_thread_ident:
        return _main_session_factory

    # Fast path: 命中且非 stale
    entry = _thread_entries.get(tid)
    if entry is not None and not _is_entry_stale(entry):
        return entry.session_factory

    # Slow path: 调 get_thread_engine (会处理 stale + 创建)
    get_thread_engine()
    return _thread_entries[tid].session_factory


async def dispose_thread_engine(tid: Optional[int] = None) -> None:
    """释放指定 thread (默认当前 thread) 的 engine + connection pool (v3.5.2).

    用途:
    1. Worker 线程优雅退出 (start_workers.py shutdown hook, 当前仍为
       dead code; v3.5.2 cache-level stale 校验会兜底)
    2. 测试场景: 清理 thread-local engine 避免 fixture 切换 engine 后脏连接

    Args:
        tid: 要释放的 thread ident. 默认当前 thread.
    """
    if tid is None:
        tid = threading.get_ident()
    with _entries_lock:
        entry = _thread_entries.pop(tid, None)
    if entry is None:
        return
    try:
        await entry.engine.dispose()
        logger.info("[engine] dispose thread-local engine (tid=%s)", tid)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[engine] dispose thread-local engine failed (tid=%s): %s",
            tid, e,
        )


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

