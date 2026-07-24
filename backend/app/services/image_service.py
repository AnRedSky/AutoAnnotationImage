"""
ImageService — 图片业务编排服务
=============================

**职责**:
- 图片 CRUD 编排
- 标注写入 (含 AI / 人工 / 人工修正 三种来源)
- 状态机入口 (mark_ai_labeled / mark_confirmed / mark_corrected)
- 触发 AnnotationLog (审计)
- 自动联动 DatasetService.refresh_statistics

**关键设计**:
- ORM 业务方法 (image.mark_confirmed) 只改字段, 不写日志
- 业务编排 (写 log + 刷统计 + 触发回调) 全部在本服务

**API 接入**:
- 旧: `app/api/image.py` 48KB (1147 行), 业务与 HTTP 紧耦合
- 新: API 层 thin wrapper, 业务全部下沉

v3.0.0 Phase 3 新增
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.annotation_log import AnnotationLog
from app.model.image import Image, IMAGE_STATUS_CONFIRMED
from app.services.dataset_service import DatasetService

logger = logging.getLogger(__name__)


class ImageService:
    """图片业务编排服务 (无状态, 静态方法)"""

    # ============== 查询 ==============

    @staticmethod
    async def get(db: AsyncSession, image_id: int) -> Optional[Image]:
        return await db.get(Image, image_id)

    @staticmethod
    async def list_by_dataset(
        db: AsyncSession,
        dataset_id: int,
        *,
        statuses: Optional[tuple[str, ...]] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Image]:
        from app.model.image_queries import list_images_by_dataset
        return await list_images_by_dataset(
            db, dataset_id, statuses=statuses, skip=skip, limit=limit,
        )

    @staticmethod
    async def list_next_pending(
        db: AsyncSession, dataset_id: int, limit: int = 200
    ) -> List[Image]:
        """列出待标注的下一批 (前端标注界面用)"""
        from app.model.image_queries import list_pending_images
        return await list_pending_images(db, dataset_id, limit=limit)

    # ============== 标注写入 (核心) ==============

    @staticmethod
    async def mark_ai_labeled(
        db: AsyncSession,
        image: Image,
        prediction: Dict[str, Any],
        *,
        commit: bool = True,
    ) -> Image:
        """AI 推理后写入预测 (供 auto-annotate / 候选标签场景)

        Args:
            image: 已加载的 Image ORM
            prediction: AI 预测 dict, 至少含 top1 / top1_conf
        """
        image.mark_ai_labeled(prediction)
        if commit:
            await db.commit()
            await db.refresh(image)
        return image

    @staticmethod
    async def mark_confirmed(
        db: AsyncSession,
        image: Image,
        *,
        user_id: int,
        label_id: Optional[int] = None,
        time_spent_ms: int = 0,
        commit: bool = True,
    ) -> Image:
        """人工确认标注 (核心业务路径)

        业务规则:
        1. 调用 image.mark_confirmed 改 status
        2. 写 AnnotationLog (审计: action=confirm, from→to)
        3. 刷新 dataset 统计
        """
        from_label_id = image.final_label_id  # 记入审计
        image.mark_confirmed(user_id=user_id, label_id=label_id)

        log = AnnotationLog(
            image_id=image.id,
            user_id=user_id,
            action="confirm",
            from_label_id=from_label_id,
            to_label_id=label_id,
            time_spent_ms=time_spent_ms,
        )
        db.add(log)
        if commit:
            await db.commit()
            await db.refresh(image)
        # 统计刷新 (失败不阻塞)
        try:
            await DatasetService.refresh_statistics(db, image.dataset_id)
        except Exception:
            logger.exception("Failed to refresh dataset %s stats", image.dataset_id)
        return image

    @staticmethod
    async def mark_corrected(
        db: AsyncSession,
        image: Image,
        *,
        user_id: int,
        label_id: Optional[int] = None,
        time_spent_ms: int = 0,
        commit: bool = True,
    ) -> Image:
        """人工修正 (与 confirm 类似, 但 action=correct)"""
        from_label_id = image.final_label_id
        image.mark_corrected(user_id=user_id, label_id=label_id)

        log = AnnotationLog(
            image_id=image.id,
            user_id=user_id,
            action="correct",
            from_label_id=from_label_id,
            to_label_id=label_id,
            time_spent_ms=time_spent_ms,
        )
        db.add(log)
        if commit:
            await db.commit()
            await db.refresh(image)
        try:
            await DatasetService.refresh_statistics(db, image.dataset_id)
        except Exception:
            logger.exception("Failed to refresh dataset %s stats", image.dataset_id)
        return image

    @staticmethod
    async def mark_rejected(
        db: AsyncSession,
        image: Image,
        *,
        user_id: int,
        time_spent_ms: int = 0,
        commit: bool = True,
    ) -> Image:
        """驳回 AI 预测 (回退到 pending)"""
        from_label_id = image.final_label_id
        image.status = "pending"
        image.ai_prediction = None  # 清掉预测, 重新进入待标注

        log = AnnotationLog(
            image_id=image.id,
            user_id=user_id,
            action="reject",
            from_label_id=from_label_id,
            to_label_id=None,
            time_spent_ms=time_spent_ms,
        )
        db.add(log)
        if commit:
            await db.commit()
            await db.refresh(image)
        return image

    # ============== AI 候选标签 ==============

    @staticmethod
    async def get_ai_candidates(
        db: AsyncSession,
        image: Image,
        *,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """获取 AI 候选标签 (前 top_k)

        分类任务: 从 image.ai_prediction.topk 读
        检测任务: 从 image.ai_prediction.boxes 读 (caller 用 bbox_service 格式化)
        """
        if not image.has_ai_prediction():
            return []
        pred = image.ai_prediction or {}
        topk = pred.get("topk") or []
        if topk:
            return topk[:top_k]
        # 兼容单 top1
        top1 = pred.get("top1")
        if top1:
            return [{"label": top1, "confidence": pred.get("top1_conf", 0.0)}]
        return []

    # ============== 统计 ==============

    @staticmethod
    async def count_by_status(
        db: AsyncSession, dataset_id: int,
    ) -> Dict[str, int]:
        from app.model.image_queries import count_images_by_status
        return await count_images_by_status(db, dataset_id)


__all__ = ["ImageService"]
