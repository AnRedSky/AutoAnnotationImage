"""
AnnotationService — 标注业务统一入口 (app/annotation/service/)
=============================================================

**职责**:
- 三类任务 (classification / detection / segmentation) 标注统一入口
- 派发到 tasks 应用的 ImageService / DetectionService / SegmentationService
- 写 AnnotationLog (审计)

**架构**:
- v3.0.0 审查修复: 之前位于 `app.tasks.service.annotation_service`,
  导致 `app.annotation.service` 跨应用 import tasks service, 违反
  `app.annotation.__init__` 文档化的硬约束:
  > 不直接 import app.tasks.service, 而是通过 app.tasks.model 引用模型
- 现迁入 annotation 应用, 由 annotation 应用反向调用 tasks 数据服务
  (annotation 依赖 tasks 数据层, 方向正确, 不构成循环)

**关键设计**:
- 不重复实现各任务的标注逻辑, 而是把已有 Service 组合起来
- 后续可在此层加统一业务规则 (如: "AI 置信度 < 0.5 的图必须人工确认")
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.image import Image
from app.tasks.service.image_service import ImageService
from app.tasks.service.detection_service import DetectionService
from app.tasks.service.segmentation_service import SegmentationService

logger = logging.getLogger(__name__)


class AnnotationService:
    """标注业务统一入口 (无状态, 静态方法)"""

    @staticmethod
    async def save_classification_annotation(
        db: AsyncSession,
        image: Image,
        *,
        user_id: int,
        label_id: int,
        action: str = "confirm",  # 'confirm' / 'correct'
        time_spent_ms: int = 0,
    ) -> Image:
        """分类任务标注 (单标签)

        派发到 ImageService.mark_confirmed / mark_corrected
        """
        if action == "confirm":
            return await ImageService.mark_confirmed(
                db, image, user_id=user_id, label_id=label_id, time_spent_ms=time_spent_ms,
            )
        elif action == "correct":
            return await ImageService.mark_corrected(
                db, image, user_id=user_id, label_id=label_id, time_spent_ms=time_spent_ms,
            )
        elif action == "reject":
            return await ImageService.mark_rejected(
                db, image, user_id=user_id, time_spent_ms=time_spent_ms,
            )
        else:
            raise HTTPException(400, f"Invalid action: {action}")

    @staticmethod
    async def save_detection_annotation(
        db: AsyncSession,
        image: Image,
        bboxes: List[Dict[str, Any]],
        *,
        user_id: int,
        action: str = "confirm",
    ) -> List:
        """检测任务标注 (多 BBox)

        派发到 DetectionService.save_human_bboxes
        """
        return await DetectionService.save_human_bboxes(
            db, image, bboxes, user_id=user_id, action=action,
        )

    @staticmethod
    async def save_segmentation_annotation(
        db: AsyncSession,
        image: Image,
        mask_array,
        *,
        user_id: int,
        action: str = "confirm",
    ):
        """分割任务标注 (mask 数组)

        派发到 SegmentationService.save_human_mask
        """
        return await SegmentationService.save_human_mask(
            db, image, mask_array, user_id=user_id, action=action,
        )

    @staticmethod
    async def save_ai_prediction(
        db: AsyncSession,
        image: Image,
        prediction: Any,  # 任务类型不同
        *,
        user_id: Optional[int] = None,
        task_type: str = "classification",
        commit: bool = True,
    ) -> Any:
        """统一 AI 预测写入入口 (按 task_type 派发)

        - classification: prediction = dict {top1, top1_conf, topk}
        - detection:      prediction = list of bbox dicts
        - segmentation:   prediction = 2D numpy array
        """
        if task_type == "classification":
            return await ImageService.mark_ai_labeled(
                db, image, prediction, commit=commit,
            )
        elif task_type == "detection":
            return await DetectionService.save_ai_predictions(
                db, image, prediction, user_id=user_id, commit=commit,
            )
        elif task_type == "segmentation":
            return await SegmentationService.save_ai_mask(
                db, image, prediction, user_id=user_id, commit=commit,
            )
        else:
            raise HTTPException(400, f"Unknown task_type: {task_type}")


__all__ = ["AnnotationService"]
