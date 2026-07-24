"""
Category 查询函数 (Data Layer)
=============================
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.category import Category


async def list_categories_by_dataset(
    db: AsyncSession, dataset_id: int
) -> list[Category]:
    """列出某数据集的全部类别 (按 sort_order 排序)"""
    result = await db.execute(
        select(Category)
        .where(Category.dataset_id == dataset_id)
        .order_by(Category.sort_order.asc(), Category.id.asc())
    )
    return list(result.scalars().all())


async def get_category_by_id(
    db: AsyncSession, category_id: int
) -> Optional[Category]:
    return await db.get(Category, category_id)


async def get_category_by_name(
    db: AsyncSession, dataset_id: int, name: str
) -> Optional[Category]:
    """按 (dataset_id, name) 查类别 (避免重复创建)"""
    result = await db.execute(
        select(Category).where(
            Category.dataset_id == dataset_id, Category.name == name
        )
    )
    return result.scalar_one_or_none()
