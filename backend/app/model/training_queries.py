"""
TrainingJob 查询函数 (Data Layer)
================================
"""
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.training_job import TrainingJob


async def get_training_job_by_id(
    db: AsyncSession, job_id: int
) -> Optional[TrainingJob]:
    return await db.get(TrainingJob, job_id)


async def get_training_job_by_celery_id(
    db: AsyncSession, celery_task_id: str
) -> Optional[TrainingJob]:
    """按 Celery task_id 反查 ORM 行 (worker 回调时用)"""
    result = await db.execute(
        select(TrainingJob).where(TrainingJob.celery_task_id == celery_task_id)
    )
    return result.scalar_one_or_none()


async def list_jobs_by_user(
    db: AsyncSession, user_id: int, limit: int = 50
) -> list[TrainingJob]:
    """按用户列出训练任务 (按 created_at desc)"""
    result = await db.execute(
        select(TrainingJob)
        .where(TrainingJob.user_id == user_id)
        .order_by(TrainingJob.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_jobs_by_dataset(
    db: AsyncSession, dataset_id: int, limit: int = 50
) -> list[TrainingJob]:
    """按数据集列出训练任务"""
    result = await db.execute(
        select(TrainingJob)
        .where(TrainingJob.dataset_id == dataset_id)
        .order_by(TrainingJob.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_active_jobs(db: AsyncSession) -> list[TrainingJob]:
    """列出进行中的任务 (PENDING + PROGRESS + PAUSED)"""
    from app.model.training_job import (
        TRAIN_STATE_PENDING, TRAIN_STATE_PROGRESS, TRAIN_STATE_PAUSED,
    )
    result = await db.execute(
        select(TrainingJob).where(
            TrainingJob.state.in_((TRAIN_STATE_PENDING, TRAIN_STATE_PROGRESS, TRAIN_STATE_PAUSED))
        )
    )
    return list(result.scalars().all())


async def count_jobs_by_state(
    db: AsyncSession, dataset_id: Optional[int] = None
) -> dict[str, int]:
    """按状态统计训练任务数"""
    stmt = select(TrainingJob.state, func.count(TrainingJob.id))
    if dataset_id is not None:
        stmt = stmt.where(TrainingJob.dataset_id == dataset_id)
    stmt = stmt.group_by(TrainingJob.state)
    result = await db.execute(stmt)
    return {row[0]: row[1] for row in result.all()}
