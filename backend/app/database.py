"""
Database Connection (Async SQLAlchemy 2.0)
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

# 注意：app.models.* 用 `from app.database import Base`，所以这里不能 top-level
# import app.models（会循环 import）。改为在 init_db() 内部 lazy import。


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
        "pool_size": 10,
        "max_overflow": 20,
    }


engine = create_async_engine(
    settings.EFFECTIVE_DATABASE_URL,
    echo=settings.APP_DEBUG,
    **_build_engine_kwargs(),
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类"""
    pass


async def init_db():
    """Create all tables (开发环境使用, 生产用 Alembic 迁移)"""
    # 必须在 create_all 之前 import models，避免 Base.metadata 为空导致建不出表
    # 放在函数内部是为了规避循环 import：models 包内部 import 了 Base
    import app.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入: 获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
