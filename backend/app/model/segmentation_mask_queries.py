"""
SegmentationMask 查询函数 (Data Layer)
=====================================
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.segmentation_mask import SegmentationMask


async def get_mask_by_image(
    db: AsyncSession, image_id: int
) -> Optional[SegmentationMask]:
    """按 image 查 mask (1:1 关系, 至多一条)"""
    result = await db.execute(
        select(SegmentationMask).where(SegmentationMask.image_id == image_id)
    )
    return result.scalar_one_or_none()


async def list_masks_by_image_ids(
    db: AsyncSession, image_ids: list[int]
) -> list[SegmentationMask]:
    """批量查多个 image 的 mask (避免 N+1)"""
    if not image_ids:
        return []
    result = await db.execute(
        select(SegmentationMask).where(SegmentationMask.image_id.in_(image_ids))
    )
    return list(result.scalars().all())
