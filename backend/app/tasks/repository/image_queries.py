"""
Image 查询函数 (Data Layer) — app/tasks/repository/
=================================================

**v3.0.0 Stage 2.4 迁移**: 从 app/model/image_queries.py 迁入 tasks 应用
"""
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.tasks.model.image import Image, IMAGE_STATUS_VALUES, IMAGE_STATUS_CONFIRMED


async def get_image_by_id(db: AsyncSession, image_id: int) -> Optional[Image]:
    return await db.get(Image, image_id)


async def list_images_by_dataset(
    db: AsyncSession, dataset_id: int,
    statuses: Optional[tuple[str, ...]] = None,
    skip: int = 0, limit: int = 100,
) -> list[Image]:
    """按数据集查图 (可选状态过滤)"""
    stmt = select(Image).where(Image.dataset_id == dataset_id)
    if statuses:
        stmt = stmt.where(Image.status.in_(statuses))
    stmt = stmt.order_by(Image.id.asc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_confirmed_images(
    db: AsyncSession, dataset_id: int, skip: int = 0, limit: int = 1000
) -> list[Image]:
    """列出已确认的图 (用于训练/导出)"""
    return await list_images_by_dataset(
        db, dataset_id, statuses=IMAGE_STATUS_CONFIRMED, skip=skip, limit=limit
    )


async def list_pending_images(
    db: AsyncSession, dataset_id: int, limit: int = 200
) -> list[Image]:
    """列出待标注的图"""
    return await list_images_by_dataset(
        db, dataset_id, statuses=("pending",), limit=limit
    )


async def count_images_by_dataset(
    db: AsyncSession, dataset_id: int, statuses: Optional[tuple[str, ...]] = None
) -> int:
    """按数据集统计图数 (可选状态过滤)"""
    stmt = select(func.count(Image.id)).where(Image.dataset_id == dataset_id)
    if statuses:
        stmt = stmt.where(Image.status.in_(statuses))
    result = await db.execute(stmt)
    return result.scalar_one() or 0


async def count_images_by_status(
    db: AsyncSession, dataset_id: int
) -> dict[str, int]:
    """按状态统计图数 (返回 {status: count})"""
    stmt = (
        select(Image.status, func.count(Image.id))
        .where(Image.dataset_id == dataset_id)
        .group_by(Image.status)
    )
    result = await db.execute(stmt)
    return {row[0]: row[1] for row in result.all()}
