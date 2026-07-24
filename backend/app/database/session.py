"""
Database Session & Init (Database Layer)
========================================

提供 FastAPI 依赖注入的 get_db() 与应用启动时的 init_db().

v3.0.0 迁移: 从 app.database 拆出 (Phase 1.9)
"""
from app.core.config import settings
from app.database.engine import engine, AsyncSessionLocal
from app.model.base import Base


async def get_db():
    """FastAPI 依赖注入: 获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


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
    import app.model  # noqa: F401
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


__all__ = ["get_db", "init_db"]
