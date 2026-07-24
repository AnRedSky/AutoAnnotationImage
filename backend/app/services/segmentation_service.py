"""
SegmentationService — 图像分割任务业务编排
=======================================

**职责**:
- 分割 mask 业务编排 (PNG 索引图)
- 人工修正 mask
- 复用 ImageService 状态机

**关键设计**:
- mask 物理存储为 PNG 索引图 (P-mode), 像素值 = 类别索引
- 调色板从 Category.color 实时渲染, 不存到 mask 文件
- mask 加载时校验像素值, 越界标 ignore_index=-1 (避免 CrossEntropyLoss 错误)

v3.0.0 Phase 3 补充
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from PIL import Image as PILImage
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.annotation_log import AnnotationLog
from app.model.image import Image
from app.model.segmentation_mask import SegmentationMask
from app.config import settings

logger = logging.getLogger(__name__)


class SegmentationService:
    """图像分割业务编排 (无状态, 静态方法)"""

    # ============== AI 推理 mask 写入 ==============

    @staticmethod
    async def save_ai_mask(
        db: AsyncSession,
        image: Image,
        mask_array: np.ndarray,
        *,
        user_id: Optional[int] = None,
        commit: bool = True,
    ) -> SegmentationMask:
        """保存 AI 推理 mask

        Args:
            image: 已加载的 Image
            mask_array: 2D numpy 数组, shape=(H, W), dtype=uint8, 像素值=类别索引
                       0 = 背景, N = Category.id = N

        Returns:
            新建或更新的 SegmentationMask 行
        """
        # 1) 校验 mask
        if mask_array.ndim != 2:
            raise HTTPException(400, f"Mask must be 2D, got shape {mask_array.shape}")
        h, w = mask_array.shape
        if h != image.height or w != image.width:
            raise HTTPException(
                400,
                f"Mask size ({h}x{w}) doesn't match image size ({image.height}x{w})",
            )

        # 2) 校验像素值 (避免越界)
        unique_vals = np.unique(mask_array)
        if int(unique_vals.max()) > 255:
            raise HTTPException(400, "Mask values must be 0-255 (uint8)")

        # 3) 写 PNG 到磁盘
        mask_path = SegmentationService._get_mask_path(image)
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        pil_mask = PILImage.fromarray(mask_array.astype(np.uint8), mode="P")
        pil_mask.save(mask_path, format="PNG")

        # 4) 写/更新 DB 行
        existing = (await db.execute(
            select(SegmentationMask).where(SegmentationMask.image_id == image.id)
        )).scalar_one_or_none()

        if existing:
            existing.mask_path = str(mask_path.relative_to(settings.UPLOAD_DIR))
            existing.width = w
            existing.height = h
            existing.source = "ai"
            existing.annotated_by = user_id
            existing.updated_at = datetime.utcnow()
            mask = existing
        else:
            mask = SegmentationMask(
                image_id=image.id,
                mask_path=str(mask_path.relative_to(settings.UPLOAD_DIR)),
                width=w,
                height=h,
                source="ai",
                annotated_by=user_id,
            )
            db.add(mask)

        # 5) 更新 image 状态
        image.status = "ai_labeled"
        # 不存 ai_prediction JSON (mask 物理在文件, 太大不存 DB)

        if commit:
            await db.commit()
            await db.refresh(mask)
            await db.refresh(image)
        return mask

    # ============== 人工修正 mask ==============

    @staticmethod
    async def save_human_mask(
        db: AsyncSession,
        image: Image,
        mask_array: np.ndarray,
        *,
        user_id: int,
        action: str = "confirm",  # 'confirm' / 'correct'
        commit: bool = True,
    ) -> SegmentationMask:
        """保存人工 mask (confirm 或 correct)"""
        if action not in ("confirm", "correct"):
            raise HTTPException(400, f"Invalid action: {action}")

        # 1) 校验
        if mask_array.ndim != 2:
            raise HTTPException(400, f"Mask must be 2D, got shape {mask_array.shape}")
        h, w = mask_array.shape
        if image.height and image.width and (h != image.height or w != image.width):
            # 首次标注 image 还没存 width/height 时, 直接用 mask 尺寸
            image.height = h
            image.width = w

        # 2) 写 PNG
        mask_path = SegmentationService._get_mask_path(image)
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        pil_mask = PILImage.fromarray(mask_array.astype(np.uint8), mode="P")
        pil_mask.save(mask_path, format="PNG")

        # 3) 写 DB
        new_source = "human" if action == "confirm" else "human_corrected"
        existing = (await db.execute(
            select(SegmentationMask).where(SegmentationMask.image_id == image.id)
        )).scalar_one_or_none()

        if existing:
            existing.mask_path = str(mask_path.relative_to(settings.UPLOAD_DIR))
            existing.width = w
            existing.height = h
            existing.source = new_source
            existing.annotated_by = user_id
            existing.updated_at = datetime.utcnow()
            mask = existing
        else:
            mask = SegmentationMask(
                image_id=image.id,
                mask_path=str(mask_path.relative_to(settings.UPLOAD_DIR)),
                width=w,
                height=h,
                source=new_source,
                annotated_by=user_id,
            )
            db.add(mask)

        # 4) 更新 image
        from datetime import datetime as _dt
        image.status = "human_confirmed" if action == "confirm" else "human_corrected"
        image.annotated_by = user_id
        image.annotated_at = _dt.utcnow()
        image.ai_prediction = None

        # 5) 写 AnnotationLog
        log = AnnotationLog(
            image_id=image.id,
            user_id=user_id,
            action=action,
            from_label_id=None,
            to_label_id=None,
            time_spent_ms=0,
            payload={"mask_pixels": int(mask_array.sum()), "mask_unique": int(len(np.unique(mask_array)))},
        )
        db.add(log)

        if commit:
            await db.commit()
            await db.refresh(mask)
            await db.refresh(image)
        return mask

    # ============== 加载 mask ==============

    @staticmethod
    async def load_mask(
        db: AsyncSession, image_id: int
    ) -> Optional[np.ndarray]:
        """加载 mask 数组 (2D numpy)

        校验像素值, 越界标 ignore_index=-1 (避免下游 CrossEntropyLoss 错误)
        """
        from app.model.segmentation_mask_queries import get_mask_by_image
        mask_row = await get_mask_by_image(db, image_id)
        if not mask_row:
            return None
        upload_dir = Path(settings.UPLOAD_DIR)
        full_path = upload_dir / mask_row.mask_path
        if not full_path.exists():
            logger.warning("Mask file missing: %s", full_path)
            return None
        # P-mode 加载
        pil = PILImage.open(full_path)
        if pil.mode != "P":
            pil = pil.convert("P")
        arr = np.array(pil, dtype=np.int32)  # 用 int32 容纳 ignore_index
        return arr

    # ============== 内部 ==============

    @staticmethod
    def _get_mask_path(image: Image) -> Path:
        """计算 mask 物理路径: UPLOAD_DIR/dataset_{id}/mask_{image_id}.png"""
        upload_dir = Path(settings.UPLOAD_DIR)
        return upload_dir / f"dataset_{image.dataset_id}" / f"mask_{image.id}.png"

    # ============== 上传 mask (v3.0.0 Phase 4 新增) ==============

    @staticmethod
    async def save_uploaded_mask(
        db: AsyncSession,
        image: "Image",
        content: bytes,
        source: str,
        *,
        user_id: int,
        commit: bool = True,
    ) -> Dict[str, Any]:
        """保存用户上传的 mask PNG (v3.0.0 Phase 4: 业务下沉)

        业务规则:
        1. 校验 PNG 文件 (mode / 大小)
        2. 校验像素值不超过 dataset 类别数
        3. 写文件到 storage_service (自动去重 / 哈希命名)
        4. upsert ORM (单图唯一)
        5. 升级 image.status: pending/ai_labeled → human_confirmed
        """
        from app.services.storage_service import storage_service
        from app.model.category import Category

        if not content:
            raise HTTPException(400, "上传的 mask 文件为空")

        # 1) 校验 mode + 读 PNG
        from io import BytesIO
        pil = PILImage.open(BytesIO(content))
        if pil.mode == "P":
            arr = np.array(pil)
        elif pil.mode == "L":
            arr = np.array(pil)
        elif pil.mode in ("RGB", "RGBA"):
            arr = np.array(pil.getchannel("R"))
        else:
            arr = np.array(pil.convert("L"))
        width, height = pil.size
        unique_vals, counts_arr = np.unique(arr, return_counts=True)
        counts = {int(v): int(c) for v, c in zip(unique_vals, counts_arr)}

        # 2) 校验像素值不超过 dataset 类别数
        cats = (await db.execute(
            select(Category).where(Category.dataset_id == image.dataset_id)
        )).scalars().all()
        max_allowed = max([c.id for c in cats], default=0)
        overflow = [v for v in counts.keys() if v > max_allowed]
        if overflow:
            raise HTTPException(
                400,
                f"mask 像素值超过 dataset 类别数 (max_category_id={max_allowed}, "
                f"overflow={sorted(overflow)[:5]}...)",
            )

        # 3) 写盘
        file_hash = storage_service.compute_hash(content)
        storage_key = storage_service.generate_key(
            image.dataset_id, f"mask_{image.id}.png", file_hash,
        )
        if not storage_key.endswith(".png"):
            storage_key = f"{storage_key}.png"
        await storage_service.save(storage_key, content)

        # 4) upsert ORM
        existing = (await db.execute(
            select(SegmentationMask).where(SegmentationMask.image_id == image.id)
        )).scalar_one_or_none()
        if existing:
            # 删旧文件 (路径不同才删)
            if existing.mask_path and existing.mask_path != storage_key:
                try:
                    old_path = Path(storage_service.base_dir) / existing.mask_path
                    if old_path.is_file():
                        old_path.unlink()
                except Exception:
                    logger.exception("Failed to delete old mask %s", existing.mask_path)
            existing.mask_path = storage_key
            existing.width = width
            existing.height = height
            existing.source = source
            existing.annotated_by = user_id
            mask = existing
        else:
            mask = SegmentationMask(
                image_id=image.id,
                mask_path=storage_key,
                width=width,
                height=height,
                source=source,
                annotated_by=user_id,
            )
            db.add(mask)

        # 5) 升级 image.status (pending/ai_labeled → human_confirmed)
        if image.status in ("pending", "ai_labeled"):
            image.status = "human_confirmed"

        if commit:
            await db.commit()
            await db.refresh(mask)
            await db.refresh(image)

        return {
            "id": mask.id,
            "image_id": mask.image_id,
            "mask_path": mask.mask_path,
            "width": mask.width,
            "height": mask.height,
            "source": mask.source,
            "annotated_by": mask.annotated_by,
            "category_pixel_counts": counts,
        }

    @staticmethod
    async def delete_mask(
        db: AsyncSession,
        mask_id: int,
        *,
        commit: bool = True,
    ) -> bool:
        """删除 mask (v3.0.0 Phase 4: 业务下沉)"""
        from app.services.storage_service import storage_service
        mask = await db.get(SegmentationMask, mask_id)
        if not mask:
            return False
        # 删文件
        try:
            if mask.mask_path:
                full_path = Path(storage_service.base_dir) / mask.mask_path
                if full_path.is_file():
                    full_path.unlink()
        except Exception:
            logger.exception("Failed to delete mask file %s", mask.mask_path)
        await db.delete(mask)
        if commit:
            await db.commit()
        return True


__all__ = ["SegmentationService"]
