"""
Database Engine (Database Layer)
================================

创建 async SQLAlchemy engine + session factory.

v3.0.0 迁移: 从 app.database 拆出 (Phase 1.9)
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.core.config import settings


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


engine = create_async_engine(
    settings.EFFECTIVE_DATABASE_URL,
    echo=settings.APP_DEBUG,
    **_build_engine_kwargs(),
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


# Stage 5.4: 绑定慢 SQL 监控 (event listener)
# 必须在 engine 创建后立即绑定, 后续会话才生效
try:
    from app.database.slow_sql import setup_slow_sql_monitor
    setup_slow_sql_monitor(engine)
except Exception as e:  # noqa: BLE001
    import logging
    logging.getLogger(__name__).warning(f"setup_slow_sql_monitor failed: {e!r}")


# Re-export for convenience
__all__ = ["engine", "AsyncSessionLocal"]
