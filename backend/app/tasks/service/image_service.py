"""
ImageService — 图片业务编排服务 (app/tasks/service/)
===================================================

**v3.0.0 Stage 2.4 迁移**: 从 app/services/image_service.py 迁入 tasks 应用
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.annotation_log import AnnotationLog
from app.tasks.model.image import Image, IMAGE_STATUS_CONFIRMED
from app.tasks.service.dataset_service import DatasetService
from app.tasks.repository.image_queries import (
    list_images_by_dataset,
    list_pending_images,
    count_images_by_status,
)

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
        return await list_images_by_dataset(
            db, dataset_id, statuses=statuses, skip=skip, limit=limit,
        )

    @staticmethod
    async def list_next_pending(
        db: AsyncSession, dataset_id: int, limit: int = 200
    ) -> List[Image]:
        """列出待标注的下一批 (前端标注界面用)"""
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
        """AI 推理后写入预测 (供 auto-annotate / 候选标签场景)"""
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
        """人工确认标注 (核心业务路径)"""
        from_label_id = image.final_label_id
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
        image.ai_prediction = None

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
        """获取 AI 候选标签 (前 top_k)"""
        if not image.has_ai_prediction():
            return []
        pred = image.ai_prediction or {}
        topk = pred.get("topk") or []
        if topk:
            return topk[:top_k]
        top1 = pred.get("top1")
        if top1:
            return [{"label": top1, "confidence": pred.get("top1_conf", 0.0)}]
        return []

    # ============== 统计 ==============

    @staticmethod
    async def count_by_status(
        db: AsyncSession, dataset_id: int,
    ) -> Dict[str, int]:
        return await count_images_by_status(db, dataset_id)

    # ============== 删除 (v3.0.0 Phase 4 新增) ==============

    @staticmethod
    async def delete(
        db: AsyncSession,
        image: "Image",
        *,
        commit: bool = True,
    ) -> Dict[str, Any]:
        """删除单张图片 (业务下沉)"""
        from sqlalchemy.orm import selectinload
        from sqlalchemy import case, update
        from app.tasks.model.dataset import Dataset
        from app.tasks.service.storage_service import storage_service

        dataset_id = image.dataset_id
        was_annotated = image.status in ("human_confirmed", "human_corrected", "trained")

        # 1) 删文件
        try:
            if storage_service.exists(image.storage_path):
                await storage_service.delete(image.storage_path)
        except Exception:
            logger.exception("Failed to delete file %s", image.storage_path)

        # 2) 删 ORM (CASCADE 触发)
        await db.delete(image)
        await db.flush()

        # 3) 更新 dataset 计数 (跨 MySQL/SQLite 兼容)
        new_img_count = case(
            (Dataset.image_count - 1 < 0, 0),
            else_=Dataset.image_count - 1,
        )
        await db.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id)
            .values(image_count=new_img_count)
        )
        if was_annotated:
            new_ann_count = case(
                (Dataset.annotated_count - 1 < 0, 0),
                else_=Dataset.annotated_count - 1,
            )
            await db.execute(
                update(Dataset)
                .where(Dataset.id == dataset_id)
                .values(annotated_count=new_ann_count)
            )
        if commit:
            await db.commit()
        return {"success": True, "deleted_id": image.id}

    @staticmethod
    async def batch_delete(
        db: AsyncSession,
        image_ids: List[int],
        *,
        commit: bool = True,
    ) -> Dict[str, Any]:
        """批量删除图片 (业务下沉)"""
        from sqlalchemy import case, update, delete as sa_delete
        from app.tasks.model.dataset import Dataset
        from app.tasks.service.storage_service import storage_service

        if not image_ids:
            raise HTTPException(400, "image_ids cannot be empty")
        if len(image_ids) > 500:
            raise HTTPException(400, "Too many ids (max 500)")

        result = await db.execute(
            select(Image).where(Image.id.in_(image_ids))
        )
        images = list(result.scalars().all())
        if not images:
            return {"success": True, "deleted": 0, "missing": len(image_ids)}

        by_dataset: Dict[int, int] = {}
        annotated_count_by_ds: Dict[int, int] = {}
        for img in images:
            by_dataset[img.dataset_id] = by_dataset.get(img.dataset_id, 0) + 1
            if img.status in ("human_confirmed", "human_corrected", "trained"):
                annotated_count_by_ds[img.dataset_id] = (
                    annotated_count_by_ds.get(img.dataset_id, 0) + 1
                )

        deleted = 0
        for img in images:
            try:
                if storage_service.exists(img.storage_path):
                    await storage_service.delete(img.storage_path)
                deleted += 1
            except Exception:
                logger.exception("Failed to delete file %s", img.storage_path)

        await db.execute(sa_delete(Image).where(Image.id.in_([i.id for i in images])))

        for ds_id, cnt in by_dataset.items():
            new_img = case(
                (Dataset.image_count - cnt < 0, 0),
                else_=Dataset.image_count - cnt,
            )
            await db.execute(
                update(Dataset)
                .where(Dataset.id == ds_id)
                .values(image_count=new_img)
            )
        for ds_id, cnt in annotated_count_by_ds.items():
            new_ann = case(
                (Dataset.annotated_count - cnt < 0, 0),
                else_=Dataset.annotated_count - cnt,
            )
            await db.execute(
                update(Dataset)
                .where(Dataset.id == ds_id)
                .values(annotated_count=new_ann)
            )
        if commit:
            await db.commit()
        return {
            "success": True,
            "deleted": deleted,
            "missing": len(image_ids) - len(images),
        }


__all__ = ["ImageService"]
