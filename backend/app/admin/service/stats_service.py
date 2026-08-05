"""
StatsService — 统计业务服务 (app/admin/service/)
=============================================

**职责**:
- 数据集 / 标注 / 训练统计
- 仪表盘 / 总览数据

**v3.0.0 Stage 2.4 迁移**: 从 app/services/stats_service.py 迁入 admin 应用
**依赖说明**: StatsService 跨应用读 tasks 域的 ORM (Dataset/Image/TrainingJob),
这是允许的 (读访问), 写访问必须通过 service 层.

**v3.3.6-STATS-ISOLATION 关键变更**:
- global_overview() 标记为已废弃 (DEPRECATED)
  - 原行为: 返回全平台聚合 (datasets/images/users/training_jobs 总数)
  - 越权风险: 任何调用方都可获得全平台业务数据, 严重隐私泄露
  - 新行为: 任何统计接口均需按当前用户可见数据集过滤 (owner + team_member)
  - 替代方案: /api/stats/overview (按用户隔离) / /api/teams/{id} (按团队隔离)
  - 仅 admin only 的纯平台管理统计 (如用户总数) 由 /api/users (admin only) 提供
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.dataset import Dataset
from app.tasks.model.image import Image
from app.tasks.model.training_job import TrainingJob

logger = logging.getLogger(__name__)


class StatsService:
    """统计业务服务 (无状态, 静态方法)"""

    @staticmethod
    async def global_overview(db: AsyncSession) -> Dict[str, Any]:
        """全局概览 — v3.3.6-STATS-ISOLATION 已废弃 (DEPRECATED)

        ⚠️ 此方法返回全平台聚合数据 (datasets/images/users/training_jobs 总数),
        调用方如未做用户隔离会泄露他人业务数据, 严重越权.

        v3.3.6 起已不再被任何 API 端点调用:
          - /api/stats/overview 重写为按当前用户可见数据集过滤
          - 平台级统计请使用 /api/users (admin only, 含用户/团队/审计)
          - 团队级统计请使用 /api/teams/{id}/stats (团队成员视角)

        仅供迁移期内部调试, 不应在业务代码中调用.

        Returns:
            全平台聚合 (仅 admin 视角, 严禁对外)
        """
        import warnings
        warnings.warn(
            "StatsService.global_overview is deprecated since v3.3.6, "
            "use per-user /api/stats/overview or per-team /api/teams/{id}/stats instead",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning(
            "[stats_service] global_overview() is deprecated (v3.3.6). "
            "Use per-user /api/stats/overview instead."
        )

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
        from app.tasks.repository.training_queries import count_jobs_by_state
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
        from app.tasks.repository.image_queries import count_images_by_status
        from app.tasks.service.dataset_service import DatasetService

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
