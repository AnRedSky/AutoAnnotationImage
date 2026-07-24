"""
ModelVersion 查询函数 (Data Layer)
=================================
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.model_version import ModelVersion


async def list_versions_by_dataset(
    db: AsyncSession, dataset_id: int, task_type: Optional[str] = None
) -> list[ModelVersion]:
    """按数据集列出模型版本 (按主指标 desc)"""
    stmt = select(ModelVersion).where(ModelVersion.dataset_id == dataset_id)
    if task_type:
        stmt = stmt.where(ModelVersion.task_type == task_type)
    stmt = stmt.order_by(
        ModelVersion.map_50.desc().nulls_last(),
        ModelVersion.miou.desc().nulls_last(),
        ModelVersion.accuracy.desc(),
        ModelVersion.created_at.desc(),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_active_model(
    db: AsyncSession, dataset_id: int, task_type: str
) -> Optional[ModelVersion]:
    """获取某数据集某任务类型的当前激活模型 (按主指标降序)"""
    versions = await list_versions_by_dataset(db, dataset_id, task_type=task_type)
    for v in versions:
        if v.is_active:
            return v
    # 没有激活的, 返回主指标最高的
    if versions:
        return versions[0]
    return None


async def get_version_by_id(
    db: AsyncSession, version_id: int
) -> Optional[ModelVersion]:
    return await db.get(ModelVersion, version_id)
