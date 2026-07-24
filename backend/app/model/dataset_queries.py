"""
Dataset 查询函数 (Data Layer)
============================
"""
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.dataset import Dataset


async def get_dataset_by_id(db: AsyncSession, dataset_id: int) -> Optional[Dataset]:
    """按 ID 查数据集"""
    return await db.get(Dataset, dataset_id)


async def list_datasets_by_owner(
    db: AsyncSession, owner_id: int, skip: int = 0, limit: int = 100
) -> list[Dataset]:
    """列出某用户拥有的数据集 (分页)"""
    result = await db.execute(
        select(Dataset)
        .where(Dataset.owner_id == owner_id)
        .order_by(Dataset.created_at.desc())
        .offset(skip).limit(limit)
    )
    return list(result.scalars().all())


async def list_datasets_by_task_type(
    db: AsyncSession, task_type: str, skip: int = 0, limit: int = 100
) -> list[Dataset]:
    """按任务类型列出数据集 (分页)"""
    result = await db.execute(
        select(Dataset)
        .where(Dataset.task_type == task_type)
        .order_by(Dataset.created_at.desc())
        .offset(skip).limit(limit)
    )
    return list(result.scalars().all())


async def count_datasets_by_owner(db: AsyncSession, owner_id: int) -> int:
    """统计用户数据集数量"""
    result = await db.execute(
        select(func.count(Dataset.id)).where(Dataset.owner_id == owner_id)
    )
    return result.scalar_one() or 0


async def list_all_datasets(db: AsyncSession, limit: int = 200) -> list[Dataset]:
    """列出全部数据集 (管理用)"""
    result = await db.execute(
        select(Dataset).order_by(Dataset.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())
