"""
ModelService — 模型版本业务编排服务
==================================

**职责**:
- 模型版本 CRUD
- 激活模型 (is_active=True, 同 dataset 其它模型自动失活)
- 评估结果写入 (指标 / 混淆矩阵 / 训练历史)

**关键业务规则**:
- 同一 dataset 仅允许 1 个激活模型 (DB 唯一约束 + 业务校验双重保护)
- 激活时必须按 mAP50 desc 选最优 (检测任务), 避免误激活次优模型
- 模型权重文件路径必须相对项目根, 避免污染 backend 目录

v3.0.0 Phase 3 新增
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.model_version import ModelVersion
from app.model.model_version_queries import (
    get_active_model,
    list_versions_by_dataset as list_models_by_dataset,
)

logger = logging.getLogger(__name__)


class ModelService:
    """模型版本业务编排服务 (无状态, 静态方法)"""

    # ============== 查询 ==============

    @staticmethod
    async def get(db: AsyncSession, model_id: int) -> Optional[ModelVersion]:
        return await db.get(ModelVersion, model_id)

    @staticmethod
    async def list_by_dataset(
        db: AsyncSession, dataset_id: int, limit: int = 50
    ) -> List[ModelVersion]:
        return await list_versions_by_dataset(db, dataset_id, limit=limit)

    @staticmethod
    async def get_active_for_dataset(
        db: AsyncSession, dataset_id: int
    ) -> Optional[ModelVersion]:
        """获取数据集的激活模型

        规则: 同 dataset 仅 1 个 active, 按 mAP50 desc, created_at desc
        (检测任务用 mAP50; 分类按 accuracy; 分割按 miou, 业务上统一 mAP50)
        """
        return await get_active_model(db, dataset_id)

    # ============== 业务操作 ==============

    @staticmethod
    async def activate(
        db: AsyncSession,
        model: ModelVersion,
        *,
        commit: bool = True,
    ) -> ModelVersion:
        """激活模型 (业务规则统一入口)

        业务规则:
        1. 同 dataset 其它模型全部 is_active=False
        2. 当前模型 is_active=True
        3. 仅 dataset 匹配的模型可激活
        """
        if model.dataset_id is None:
            raise HTTPException(400, "Model has no associated dataset, cannot activate")

        # 1) 失活同 dataset 其它模型
        await db.execute(
            update(ModelVersion)
            .where(ModelVersion.dataset_id == model.dataset_id)
            .where(ModelVersion.id != model.id)
            .values(is_active=False)
        )
        # 2) 激活当前模型
        model.is_active = True

        if commit:
            await db.commit()
            await db.refresh(model)
        logger.info(
            "Model %s (dataset=%s) activated; other models deactivated",
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
        """失活模型 (反向操作)"""
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
        """更新模型评估指标 (训练完成后由 worker 调用)

        所有字段都可选, caller 只传关心的指标
        """
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
            # 兼容 numpy ndarray: 转 list
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
        """按任务类型选最优模型 (前端"切换为最佳"功能用)

        - classification: 按 accuracy desc, created_at desc
        - detection:      按 mAP50 desc, created_at desc
        - segmentation:   按 miou desc, created_at desc
        """
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
