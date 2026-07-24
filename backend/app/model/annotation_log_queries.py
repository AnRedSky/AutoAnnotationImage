"""
AnnotationLog 查询函数 (Data Layer)
==================================
"""
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.annotation_log import AnnotationLog


async def list_logs_by_image(
    db: AsyncSession, image_id: int, limit: int = 50
) -> list[AnnotationLog]:
    """按 image 查日志 (按时间 desc)"""
    result = await db.execute(
        select(AnnotationLog)
        .where(AnnotationLog.image_id == image_id)
        .order_by(AnnotationLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_logs_by_user(
    db: AsyncSession, user_id: int, limit: int = 100
) -> list[AnnotationLog]:
    """按 user 查日志"""
    result = await db.execute(
        select(AnnotationLog)
        .where(AnnotationLog.user_id == user_id)
        .order_by(AnnotationLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_logs_by_action(
    db: AsyncSession, dataset_id: Optional[int] = None
) -> dict[str, int]:
    """按 action 统计 (用于效率分析)"""
    from app.model.image import Image  # 避免循环 import
    stmt = select(AnnotationLog.action, func.count(AnnotationLog.id))
    if dataset_id is not None:
        stmt = stmt.join(Image, Image.id == AnnotationLog.image_id).where(
            Image.dataset_id == dataset_id
        )
    stmt = stmt.group_by(AnnotationLog.action)
    result = await db.execute(stmt)
    return {row[0]: row[1] for row in result.all()}
