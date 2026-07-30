"""
ModelService — 模型版本业务编排服务 (app/tasks/service/)
======================================================

**v3.0.0 Stage 2.4 迁移**: 从 app/services/model_service.py 迁入 tasks 应用
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.model_version import ModelVersion
from app.tasks.repository.model_version_queries import (
    get_active_model,
    list_active_per_dataset,  # Phase V #2: 新增 window-function query
    list_versions_by_dataset as list_models_by_dataset,
)

logger = logging.getLogger(__name__)


class ModelService:
    """模型版本业务编排服务 (无状态, 静态方法)"""

    @staticmethod
    async def get(db: AsyncSession, model_id: int) -> Optional[ModelVersion]:
        return await db.get(ModelVersion, model_id)

    @staticmethod
    async def list_by_dataset(
        db: AsyncSession, dataset_id: int, limit: int = 50
    ) -> List[ModelVersion]:
        return await list_models_by_dataset(db, dataset_id, limit=limit)

    @staticmethod
    async def get_active_for_dataset(
        db: AsyncSession,
        dataset_id: int,
        task_type: Optional[str] = None,
    ) -> Optional[ModelVersion]:
        """获取数据集的激活模型 (按 mAP50/mIoU/accuracy desc, created_at desc)

        v3.0.0 审查修复: task_type 改为可选, 与 list_active_models API 端点对齐
        """
        if task_type:
            return await get_active_model(db, dataset_id, task_type)
        # 跨任务类型时: 用 list_versions_by_dataset 内部排序
        from app.tasks.repository.model_version_queries import list_versions_by_dataset
        versions = await list_versions_by_dataset(db, dataset_id, task_type=None)
        for v in versions:
            if v.is_active:
                return v
        return versions[0] if versions else None

    @staticmethod
    async def get_active_for_all_datasets(
        db: AsyncSession,
        task_type: Optional[str] = None,
    ) -> Dict[int, ModelVersion]:
        """Phase V #2 优化: 单 query 拿全部 dataset 的「最优模型」.

        替代 ``/api/models/active`` 端点里 for-dataset 循环.
        之前: N datasets → N+1 query.
        现在: 1 query (ROW_NUMBER OVER PARTITION BY dataset_id).
        """
        return await list_active_per_dataset(db, task_type=task_type)

    @staticmethod
    async def activate(
        db: AsyncSession,
        model: ModelVersion,
        *,
        commit: bool = True,
    ) -> ModelVersion:
        """激活模型 (幂等, 允许多激活并存)

        v3.0.0 业务规则调整: 由「同 dataset 单激活不变量」改为「多激活并存」语义.
        - 取消之前的「同 dataset 互斥」update, 直接置当前行 is_active=True
        - 不影响其他模型的激活态, 用户可同时激活多个版本
        - 推理/自动标注通过显式传入 model_version_id 选择具体模型, 不依赖单激活假设
        - 兜底场景: get_active_for_dataset() / get_active_model() 仍按「第一个 active」
          回退, 保持旧调用方兼容

        Args:
            db: Async DB session
            model: 目标 ModelVersion 行 (调用方已加载)
            commit: True=立即 commit, False=留给调用方在事务中处理

        Returns:
            更新后的 ModelVersion (is_active=True)
        """
        if model.dataset_id is None:
            raise HTTPException(400, "Model has no associated dataset, cannot activate")

        model.is_active = True

        if commit:
            await db.commit()
            await db.refresh(model)
        logger.info(
            "Model %s (dataset=%s) activated (多激活并存语义)",
            model.id, model.dataset_id,
        )
        return model

    @staticmethod
    async def deactivate(
        db: AsyncSession,
        model: ModelVersion,
        *,
        commit: bool = True,
    ) -> ModelVersion:
        model.is_active = False
        if commit:
            await db.commit()
            await db.refresh(model)
        return model

    @staticmethod
    async def update_metrics(
        db: AsyncSession,
        model: ModelVersion,
        *,
        accuracy: Optional[float] = None,
        precision: Optional[float] = None,
        recall: Optional[float] = None,
        f1_score: Optional[float] = None,
        map_50: Optional[float] = None,
        map_50_95: Optional[float] = None,
        miou: Optional[float] = None,
        pixel_accuracy: Optional[float] = None,
        dice_score: Optional[float] = None,
        confusion_matrix: Optional[list] = None,
        training_log: Optional[Dict[str, Any]] = None,
        commit: bool = True,
    ) -> ModelVersion:
        """更新模型评估指标 (训练完成后由 worker 调用)"""
        if accuracy is not None:
            model.accuracy = accuracy
        if precision is not None:
            model.precision = precision
        if recall is not None:
            model.recall = recall
        if f1_score is not None:
            model.f1_score = f1_score
        if map_50 is not None:
            model.map_50 = map_50
        if map_50_95 is not None:
            model.map_50_95 = map_50_95
        if miou is not None:
            model.miou = miou
        if pixel_accuracy is not None:
            model.pixel_accuracy = pixel_accuracy
        if dice_score is not None:
            model.dice_score = dice_score
        if confusion_matrix is not None:
            try:
                import numpy as np
                if isinstance(confusion_matrix, np.ndarray):
                    confusion_matrix = confusion_matrix.tolist()
            except ImportError:
                pass
            model.confusion_matrix = confusion_matrix
        if training_log is not None:
            model.training_log = training_log

        if commit:
            await db.commit()
            await db.refresh(model)
        return model

    @staticmethod
    async def get_best_model(
        db: AsyncSession, dataset_id: int, task_type: str,
    ) -> Optional[ModelVersion]:
        """按任务类型选最优模型 (前端"切换为最佳"功能用)"""
        if task_type == "detection":
            order_col = ModelVersion.map_50
        elif task_type == "segmentation":
            order_col = ModelVersion.miou
        else:
            order_col = ModelVersion.accuracy
        result = await db.execute(
            select(ModelVersion)
            .where(ModelVersion.dataset_id == dataset_id)
            .where(ModelVersion.task_type == task_type)
            .order_by(order_col.desc(), ModelVersion.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


__all__ = ["ModelService"]
