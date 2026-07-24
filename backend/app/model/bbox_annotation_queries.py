"""
BBoxAnnotation 查询函数 (Data Layer)
===================================
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.bbox_annotation import BBoxAnnotation


async def list_bboxes_by_image(
    db: AsyncSession, image_id: int
) -> list[BBoxAnnotation]:
    """按 image 查全部 bbox (按 id asc)"""
    result = await db.execute(
        select(BBoxAnnotation)
        .where(BBoxAnnotation.image_id == image_id)
        .order_by(BBoxAnnotation.id.asc())
    )
    return list(result.scalars().all())


async def list_bboxes_by_image_ids(
    db: AsyncSession, image_ids: list[int]
) -> list[BBoxAnnotation]:
    """批量查多个 image 的 bbox (避免 N+1)"""
    if not image_ids:
        return []
    result = await db.execute(
        select(BBoxAnnotation)
        .where(BBoxAnnotation.image_id.in_(image_ids))
        .order_by(BBoxAnnotation.image_id.asc(), BBoxAnnotation.id.asc())
    )
    return list(result.scalars().all())


async def count_bboxes_by_image(
    db: AsyncSession, image_id: int
) -> int:
    """统计某图的 bbox 数"""
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(BBoxAnnotation.id)).where(
            BBoxAnnotation.image_id == image_id
        )
    )
    return result.scalar_one() or 0
