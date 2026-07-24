"""
StatsService — 统计业务服务
=========================

**职责**:
- 数据集 / 标注 / 训练统计
- 仪表盘 / 总览数据
- 性能监控 (Cache hits / misses)

v3.0.0 Phase 3 补充
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.dataset import Dataset
from app.model.image import Image
from app.model.training_job import TrainingJob
from app.model.user import User

logger = logging.getLogger(__name__)


class StatsService:
    """统计业务服务 (无状态, 静态方法)"""

    @staticmethod
    async def global_overview(db: AsyncSession) -> Dict[str, Any]:
        """全局概览 (前端仪表盘用)

        Returns:
            {
                "datasets": {"total": N, "by_status": {}},
                "images":   {"total": N, "by_status": {}},
                "training_jobs": {"total": N, "by_state": {}},
                "users":    {"total": N, "active": N},
            }
        """
        # Datasets
        ds_total = (await db.execute(select(func.count(Dataset.id)))).scalar_one() or 0
        ds_by_status_rows = (await db.execute(
            select(Dataset.status, func.count(Dataset.id)).group_by(Dataset.status)
        )).all()
        ds_by_status = {row[0]: row[1] for row in ds_by_status_rows}

        # Images
        img_total = (await db.execute(select(func.count(Image.id)))).scalar_one() or 0
        img_by_status_rows = (await db.execute(
            select(Image.status, func.count(Image.id)).group_by(Image.status)
        )).all()
        img_by_status = {row[0]: row[1] for row in img_by_status_rows}

        # Training jobs
        from app.model.training_queries import count_jobs_by_state
        job_by_state = await count_jobs_by_state(db)

        # Users
        user_total = (await db.execute(select(func.count(User.id)))).scalar_one() or 0
        user_active = (await db.execute(
            select(func.count(User.id)).where(User.is_active == True)
        )).scalar_one() or 0

        return {
            "datasets": {"total": ds_total, "by_status": ds_by_status},
            "images": {"total": img_total, "by_status": img_by_status},
            "training_jobs": {"total": sum(job_by_state.values()), "by_state": job_by_state},
            "users": {"total": user_total, "active": user_active},
        }

    @staticmethod
    async def dataset_overview(db: AsyncSession, dataset_id: int) -> Dict[str, Any]:
        """单数据集统计 (前端 dataset 详情页用)"""
        from app.model.image_queries import count_images_by_status
        from app.services.dataset_service import DatasetService

        ds = await DatasetService.get(db, dataset_id)
        if not ds:
            return {"error": "Dataset not found"}

        img_by_status = await count_images_by_status(db, dataset_id)
        return {
            "dataset": {
                "id": ds.id,
                "name": ds.name,
                "task_type": ds.task_type,
                "status": ds.status,
            },
            "image_count": ds.image_count,
            "annotated_count": ds.annotated_count,
            "category_count": ds.category_count,
            "annotation_progress_pct": ds.annotation_progress_pct(),
            "by_status": img_by_status,
        }


__all__ = ["StatsService"]
