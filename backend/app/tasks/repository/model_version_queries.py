"""
ModelVersion 查询函数 (Data Layer) — app/tasks/repository/
========================================================

**v3.0.0 Stage 2.4 迁移**: 从 app/model/model_version_queries.py 迁入 tasks 应用
**v3.0.0 修复**: NULLS LAST 改用 CASE 表达式 (兼容 MySQL/PostgreSQL/SQLite)
"""
from typing import Optional
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tasks.model.model_version import ModelVersion


async def list_versions_by_dataset(
    db: AsyncSession, dataset_id: int, task_type: Optional[str] = None
) -> list[ModelVersion]:
    """按数据集列出模型版本 (按主指标 desc)

    排序规则: NULL 值排在最后 (按主指标降序, 时间倒序)
    - 分类: accuracy desc, created_at desc
    - 检测: map_50 desc (NULL last), miou desc (NULL last), accuracy desc, created_at desc
    - 分割: miou desc (NULL last), accuracy desc, created_at desc

    兼容性: 使用 `CASE WHEN col IS NULL THEN 1 ELSE 0` 而非 `NULLS LAST`
    原因: MySQL 不支持 NULLS LAST 语法 (PG 专属), CASE 表达式跨方言通用
    """
    stmt = select(ModelVersion).where(ModelVersion.dataset_id == dataset_id)
    if task_type:
        stmt = stmt.where(ModelVersion.task_type == task_type)
    stmt = stmt.order_by(
        # map_50 NULL 排最后: ASC(0=非NULL 优先) + DESC
        case((ModelVersion.map_50.is_(None), 1), else_=0),
        ModelVersion.map_50.desc(),
        # miou NULL 排最后
        case((ModelVersion.miou.is_(None), 1), else_=0),
        ModelVersion.miou.desc(),
        ModelVersion.accuracy.desc(),
        ModelVersion.created_at.desc(),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_active_model(
    db: AsyncSession, dataset_id: int, task_type: str
) -> Optional[ModelVersion]:
    """获取某数据集某任务类型的当前激活模型"""
    versions = await list_versions_by_dataset(db, dataset_id, task_type=task_type)
    for v in versions:
        if v.is_active:
            return v
    if versions:
        return versions[0]
    return None


async def get_version_by_id(
    db: AsyncSession, version_id: int
) -> Optional[ModelVersion]:
    return await db.get(ModelVersion, version_id)
