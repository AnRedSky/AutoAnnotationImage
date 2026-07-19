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
    """应用启动: 建表 + 补列迁移
    - 建新表: Base.metadata.create_all() (幂等, 只建缺失表)
    - 补已有表缺失列: ensure_v2_0_0_schema() (幂等, 检查列存在才 ADD)

    设计原因:
      v2.0.0 改动 3 张已有表的列结构. 旧 init_db 只调 create_all, 不会补列,
      导致 v1.0.0 升级用户启动后报 `Unknown column 'training_jobs.task_type'`.
      现在启动时自动跑迁移, 用户零感知.
    """
    # 必须在 create_all 之前 import models, 避免 Base.metadata 为空导致建不出表
    import app.models  # noqa: F401
    import app.core.db_migration as dbm  # noqa: PLC0415
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 补 v2.0.0 新增列 (训练任务 / 模型版本 / 图像的任务类型 + 任务专属指标)
        result = await dbm.ensure_v2_0_0_schema(conn, verbose=settings.APP_DEBUG)
        if result["added"]:
            import logging  # noqa: PLC0415
            logging.getLogger(__name__).warning(
                "[init_db] 自动补齐 v2.0.0 列: %s",
                ", ".join(result["added"]),
            )
        if result["errors"]:
            import logging  # noqa: PLC0415
            logging.getLogger(__name__).error(
                "[init_db] 迁移失败: %s", "; ".join(result["errors"]),
            )


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入: 获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
