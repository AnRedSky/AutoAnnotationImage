"""
DetectionService — 目标检测任务业务编排
====================================

**职责**:
- 目标检测任务 (YOLO) 业务编排
- BBox 标注写入 (AI 推理结果 / 人工确认 / 人工修正)
- 复用 bbox_service (BBox 几何) + ImageService (图片业务)
- 任务状态查询走 JobStateService

**关键设计**:
- BBox 几何运算复用 `app.common.geometry.bbox_service` (BBox / IoU / NMS)
- AI 推理结果 → BBoxAnnotation 走本服务的转换函数
- 人工确认/修正 → 同时更新 image.status 和 BBoxAnnotation.source

v3.0.0 Phase 3 补充
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.annotation_log import AnnotationLog
from app.annotation.model.bbox_annotation import BBoxAnnotation
from app.tasks.model.image import Image
from app.common.geometry.bbox_service import (
    BBox,
    bbox_from_dict,
    nms,
    class_wise_nms,
)

logger = logging.getLogger(__name__)


class DetectionService:
    """目标检测业务编排 (无状态, 静态方法)"""

    # ============== AI 推理结果 → BBoxAnnotation ==============

    @staticmethod
    async def save_ai_predictions(
        db: AsyncSession,
        image: Image,
        predictions: List[Dict[str, Any]],
        *,
        user_id: Optional[int] = None,
        commit: bool = True,
    ) -> List[BBoxAnnotation]:
        """把 AI 推理预测 (list of dict) 写入 BBoxAnnotation

        预测格式 (来自 YOLO / ultralytics 推理):
        [
            {"x_min": 0.1, "y_min": 0.2, "x_max": 0.5, "y_max": 0.6,
             "confidence": 0.95, "category_id": 1, "label": "cat"},
            ...
        ]

        写入规则:
        1. 先删 image 下 source='ai' 的旧记录 (新推理覆盖旧的)
        2. 写新记录 source='ai', annotated_by=user_id (若有)
        3. 更新 image.ai_prediction (存 top-level summary)
        4. 写 AnnotationLog (action=auto_annotate_pretrained/finetuned)
        """
        # 1) 删旧 AI 标注
        existing = (await db.execute(
            select(BBoxAnnotation).where(
                BBoxAnnotation.image_id == image.id,
                BBoxAnnotation.source == "ai",
            )
        )).scalars().all()
        for old in existing:
            await db.delete(old)
        if existing:
            await db.flush()

        # 2) 写新 AI 标注
        new_records: List[BBoxAnnotation] = []
        for pred in predictions:
            record = BBoxAnnotation(
                image_id=image.id,
                category_id=pred.get("category_id"),
                x_min=float(pred["x_min"]),
                y_min=float(pred["y_min"]),
                x_max=float(pred["x_max"]),
                y_max=float(pred["y_max"]),
                confidence=pred.get("confidence"),
                source="ai",
                annotated_by=user_id,
            )
            db.add(record)
            new_records.append(record)

        # 3) 更新 image.ai_prediction
        image.ai_prediction = {
            "boxes": predictions,
            "model": predictions[0].get("model_name") if predictions else None,
        }
        # 4) 标 status=ai_labeled (复用 Image 业务方法)
        image.status = "ai_labeled"

        if commit:
            await db.commit()
            for r in new_records:
                await db.refresh(r)
            await db.refresh(image)
        return new_records

    # ============== 人工确认/修正 ==============

    @staticmethod
    async def save_human_bboxes(
        db: AsyncSession,
        image: Image,
        bboxes: List[Dict[str, Any]],
        *,
        user_id: int,
        action: str = "confirm",  # 'confirm' / 'correct'
        commit: bool = True,
    ) -> List[BBoxAnnotation]:
        """保存人工 BBox 标注 (confirm 或 correct)

        流程:
        1. 删 image 下的 source='human' / 'human_corrected' 旧记录
        2. 写新记录
        3. 更新 image.status / annotated_by / annotated_at
        4. 写 AnnotationLog (action=confirm/correct)
        """
        # 校验 action
        if action not in ("confirm", "correct"):
            raise HTTPException(400, f"Invalid action: {action}, must be confirm or correct")

        # 1) 删旧人工标注
        old_sources = ("human", "human_corrected")
        existing = (await db.execute(
            select(BBoxAnnotation).where(
                BBoxAnnotation.image_id == image.id,
                BBoxAnnotation.source.in_(old_sources),
            )
        )).scalars().all()
        for old in existing:
            await db.delete(old)
        if existing:
            await db.flush()

        # 2) 写新记录
        new_records: List[BBoxAnnotation] = []
        new_source = "human" if action == "confirm" else "human_corrected"
        for bb in bboxes:
            record = BBoxAnnotation(
                image_id=image.id,
                category_id=bb.get("category_id"),
                x_min=float(bb["x_min"]),
                y_min=float(bb["y_min"]),
                x_max=float(bb["x_max"]),
                y_max=float(bb["y_max"]),
                confidence=None,  # 人工无 confidence
                source=new_source,
                annotated_by=user_id,
            )
            db.add(record)
            new_records.append(record)

        # 3) 更新 image 状态
        from datetime import datetime
        if action == "confirm":
            image.status = "human_confirmed"
        else:
            image.status = "human_corrected"
        image.annotated_by = user_id
        image.annotated_at = datetime.utcnow()
        # 清 AI prediction, 人工已确认则不再展示 AI 预测
        image.ai_prediction = None

        # v3.0.0: 写入新标注时自动清除"不合格"标记 (类别标签与不合格互斥)
        auto_unmarked = False
        if image.is_unqualified():
            image.unmark_unqualified()
            auto_unmarked = True

        # 4) 写 AnnotationLog
        log = AnnotationLog(
            image_id=image.id,
            user_id=user_id,
            action=action,
            from_label_id=None,
            to_label_id=bboxes[0].get("category_id") if bboxes else None,
            time_spent_ms=0,
            payload={"bbox_count": len(bboxes)},
        )
        db.add(log)
        if auto_unmarked:
            db.add(AnnotationLog(
                image_id=image.id,
                user_id=user_id,
                action="unmark_unqualified",
                payload={"reason": "auto_cleared_on_bbox_save"},
                time_spent_ms=0,
            ))

        if commit:
            await db.commit()
            for r in new_records:
                await db.refresh(r)
            await db.refresh(image)
        return new_records

    # ============== NMS 后处理 (供 AI 推理结果过滤) ==============

    @staticmethod
    def apply_nms(
        predictions: List[Dict[str, Any]],
        iou_threshold: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """对 AI 推理结果应用 NMS (类内 / 全类)

        Args:
            predictions: AI 推理结果 list
            iou_threshold: IoU 阈值, 超过则抑制

        Returns:
            抑制后的 predictions
        """
        bboxes = [bbox_from_dict(p) for p in predictions]
        # class_wise_nms 比 nms 慢但同类别不互相抑制 (YOLO 论文做法)
        kept_bboxes = class_wise_nms(bboxes, iou_threshold=iou_threshold)
        # 反查原 dict
        kept_set = {(b.x_min, b.y_min, b.x_max, b.y_max) for b in kept_bboxes}
        return [p for p in predictions
                if (p["x_min"], p["y_min"], p["x_max"], p["y_max"]) in kept_set]

    # ============== 查询 ==============

    @staticmethod
    async def list_bboxes(
        db: AsyncSession, image_id: int, source: Optional[str] = None
    ) -> List[BBoxAnnotation]:
        """查图的所有 BBox (可选 source 过滤)"""
        from app.tasks.model.bbox_annotation_queries import list_bbox_by_image
        if source:
            result = await db.execute(
                select(BBoxAnnotation).where(
                    BBoxAnnotation.image_id == image_id,
                    BBoxAnnotation.source == source,
                )
            )
            return list(result.scalars().all())
        return await list_bbox_by_image(db, image_id)

    # ============== 全量替换 / 清空 (v3.0.0 Phase 4 新增) ==============

    @staticmethod
    async def replace_bboxes(
        db: AsyncSession,
        image: "Image",
        items: List[Dict[str, Any]],
        *,
        user_id: int,
        commit: bool = True,
    ) -> List[BBoxAnnotation]:
        """单图 BBox 全量替换 (v3.0.0 Phase 4: 业务下沉)

        业务规则:
        1. 删除 image 下所有现有 BBox
        2. 校验所有 category_id 归属
        3. 写入新 BBox (annotated_by = user_id)
        4. v3.0.0: 自动清除不合格标记 (与人工标注互斥)
        5. 一次 commit, 事务内完成
        """
        # 1) 校验 category
        from app.tasks.model.category import Category
        for it in items:
            cat_id = it.get("category_id")
            if cat_id is not None:
                cat = await db.get(Category, cat_id)
                if not cat:
                    raise NotFoundError(f"Category id={cat_id} not found")
                if cat.dataset_id != image.dataset_id:
                    raise ValidationError(
                        f"Category id={cat_id} 不属于 dataset id={image.dataset_id}",
                    )

        # 2) 删旧 bbox
        existing = (await db.execute(
            select(BBoxAnnotation).where(BBoxAnnotation.image_id == image.id)
        )).scalars().all()
        for old in existing:
            await db.delete(old)
        if existing:
            await db.flush()

        # 3) 写新 bbox
        from app.common.enums import AnnotationSource
        new_boxes: List[BBoxAnnotation] = []
        for it in items:
            src = it.get("source") or AnnotationSource.HUMAN.value
            bb = BBoxAnnotation(
                image_id=image.id,
                category_id=it.get("category_id"),
                x_min=float(it["x_min"]),
                y_min=float(it["y_min"]),
                x_max=float(it["x_max"]),
                y_max=float(it["y_max"]),
                confidence=it.get("confidence"),
                source=src,
                annotated_by=user_id,
            )
            db.add(bb)
            new_boxes.append(bb)

        # 4) v3.0.0: 写入新标注时自动清除"不合格"标记 (类别标签与不合格互斥)
        # 写一条 unmark_unqualified 审计, 保留撤销原因
        if image.is_unqualified():
            image.unmark_unqualified()
            db.add(AnnotationLog(
                image_id=image.id,
                user_id=user_id,
                action="unmark_unqualified",
                payload={"reason": "auto_cleared_on_bbox_replace"},
                time_spent_ms=0,
            ))

        if commit:
            await db.commit()
            for r in new_boxes:
                await db.refresh(r)
        return new_boxes

    @staticmethod
    async def clear_bboxes(
        db: AsyncSession,
        image: "Image",
        *,
        commit: bool = True,
    ) -> int:
        """清空单图全部 BBox (v3.0.0 Phase 4: 业务下沉)

        配合前端 DetectionAnnotator 的「重画」流程: 先 clear 旧的, 再 save 新的
        """
        from sqlalchemy import delete as sa_delete
        result = await db.execute(
            sa_delete(BBoxAnnotation).where(BBoxAnnotation.image_id == image.id)
        )
        if commit:
            await db.commit()
        return int(result.rowcount or 0)


__all__ = ["DetectionService"]
