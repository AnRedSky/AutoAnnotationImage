"""
DatasetService — 数据集业务编排服务
=================================

**职责**:
- 数据集 CRUD 编排 (校验 / 权限 / 级联删除)
- 数据集状态机转移
- 统计刷新 (image_count / annotated_count / category_count)

**关键设计**:
- 业务规则 (如"非 draft 不能删除") 在本服务统一处理
- 级联删除走 `DatasetService.cascade_delete()`, 不用 ORM 自带 cascade
  (因为 model_version / training_job 在 MySQL DDL 上没有 CASCADE,
  必须显式顺序删, 详见 [project_memory])

**API 接入**:
- 旧: `app/api/dataset.py` 15KB, 散落 200+ 行级联删除逻辑
- 新: API 层 thin wrapper, 业务全部下沉

v3.0.0 Phase 3 新增
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.model.annotation_log import AnnotationLog
from app.model.bbox_annotation import BBoxAnnotation
from app.model.category import Category
from app.model.dataset import Dataset, DATASET_STATUS_DONE, DATASET_STATUS_DRAFT
from app.model.image import Image
from app.model.model_version import ModelVersion
from app.model.segmentation_mask import SegmentationMask
from app.model.training_job import TrainingJob
from app.model.dataset_queries import get_dataset_by_id

logger = logging.getLogger(__name__)


class DatasetService:
    """数据集业务编排服务 (无状态, 静态方法)"""

    # ============== 查询 ==============

    @staticmethod
    async def get(db: AsyncSession, dataset_id: int) -> Optional[Dataset]:
        return await get_dataset_by_id(db, dataset_id)

    @staticmethod
    async def list_by_owner(
        db: AsyncSession, owner_id: int, skip: int = 0, limit: int = 100
    ) -> List[Dataset]:
        from app.model.dataset_queries import list_datasets_by_owner
        return await list_datasets_by_owner(db, owner_id, skip=skip, limit=limit)

    # ============== 业务操作 ==============

    @staticmethod
    async def transition_status(
        db: AsyncSession,
        dataset: Dataset,
        new_status: str,
    ) -> Dataset:
        """统一状态机入口 (业务规则: 仅 draft 可删除, 详见 [Dataset.transition_to])

        失败抛 ValueError, 由 caller 决定是否转 4xx.
        """
        dataset.transition_to(new_status)
        await db.commit()
        await db.refresh(dataset)
        return dataset

    @staticmethod
    async def refresh_statistics(
        db: AsyncSession,
        dataset_id: int,
    ) -> Dataset:
        """刷新数据集统计字段 (image_count / annotated_count / category_count)

        单一真相源, 任何"图被增删/状态变更/类别被增删"路径都应调用本方法
        """
        from app.model.image_queries import count_images_by_dataset, count_images_by_status
        from sqlalchemy import func

        ds = await get_dataset_by_id(db, dataset_id)
        if not ds:
            raise HTTPException(404, f"Dataset {dataset_id} not found")

        ds.image_count = await count_images_by_dataset(db, dataset_id)
        # annotated = human_confirmed + human_corrected + trained
        from app.model.image import IMAGE_STATUS_CONFIRMED
        ds.annotated_count = await count_images_by_dataset(
            db, dataset_id, statuses=IMAGE_STATUS_CONFIRMED,
        )
        # category_count
        cat_count = (await db.execute(
            select(func.count(Category.id)).where(Category.dataset_id == dataset_id)
        )).scalar_one() or 0
        ds.category_count = cat_count

        await db.commit()
        await db.refresh(ds)
        return ds

    # ============== 级联删除 (核心) ==============

    @staticmethod
    async def cascade_delete(
        db: AsyncSession,
        dataset: Dataset,
    ) -> Dict[str, int]:
        """级联删除数据集 (业务规则统一入口)

        关键问题 (v3.0.0 修复): MySQL DDL 中只有 category.dataset_id 和
        image.dataset_id 有 ondelete CASCADE; training_job.dataset_id 和
        model_version.dataset_id 没有 CASCADE. 因此必须显式顺序删除:

        1. 清理磁盘文件 (图片 / 标注 / 模型权重)
        2. training_job (依赖 dataset, 无 CASCADE)
        3. model_version (依赖 dataset, 无 CASCADE)
        4. annotation_log (依赖 image, 但 image 无 CASCADE 反向)
        5. image (有 CASCADE, 删它会级联删 bbox_annotation / segmentation_mask)
        6. category (有 CASCADE)
        7. dataset 本身

        Returns:
            {"training_jobs": N, "model_versions": N, "images": N, "categories": N}
        """
        dataset_id = dataset.id
        counts: Dict[str, int] = {}

        # ---- 1) 删 training_job (无 CASCADE, 必须先删) ----
        result = await db.execute(
            delete(TrainingJob).where(TrainingJob.dataset_id == dataset_id)
        )
        counts["training_jobs"] = result.rowcount or 0

        # ---- 2) 删 model_version (无 CASCADE) ----
        result = await db.execute(
            delete(ModelVersion).where(ModelVersion.dataset_id == dataset_id)
        )
        counts["model_versions"] = result.rowcount or 0

        # ---- 3) 删 annotation_log (依赖 image, 但 image 无 CASCADE 反向) ----
        # 先查出该 dataset 下所有 image.id, 再删它们的 annotation_log
        img_ids_rows = (await db.execute(
            select(Image.id).where(Image.dataset_id == dataset_id)
        )).scalars().all()
        img_ids: List[int] = list(img_ids_rows)
        if img_ids:
            result = await db.execute(
                delete(AnnotationLog).where(AnnotationLog.image_id.in_(img_ids))
            )
            counts["annotation_logs"] = result.rowcount or 0

        # ---- 4) 删 image (有 CASCADE, 会自动删 bbox_annotation / segmentation_mask) ----
        result = await db.execute(
            delete(Image).where(Image.dataset_id == dataset_id)
        )
        counts["images"] = result.rowcount or 0

        # ---- 5) 删 category (有 CASCADE) ----
        result = await db.execute(
            delete(Category).where(Category.dataset_id == dataset_id)
        )
        counts["categories"] = result.rowcount or 0

        # ---- 6) 删 dataset 本身 ----
        await db.delete(dataset)
        await db.commit()

        # ---- 7) 磁盘清理 (图片目录 / 模型目录) ----
        await DatasetService._cleanup_disk_files(dataset_id)

        logger.info("Dataset %s cascade-deleted: %s", dataset_id, counts)
        return counts

    @staticmethod
    async def _cleanup_disk_files(dataset_id: int) -> None:
        """清理磁盘: 删图目录 + 关联模型文件

        失败不应阻止 DB 删除 (DB 已删, 物理文件是次要清理)
        """
        try:
            upload_dir = Path(settings.UPLOAD_DIR)
            # 约定: 图按 dataset_id 分目录
            ds_dir = upload_dir / f"dataset_{dataset_id}"
            if ds_dir.exists() and ds_dir.is_dir():
                shutil.rmtree(ds_dir, ignore_errors=True)
        except Exception:
            logger.exception("Failed to clean dataset %s upload dir", dataset_id)

        try:
            model_dir = Path(settings.MODEL_DIR)
            # 约定: 模型按 dataset_id 子目录
            ds_model_dir = model_dir / f"dataset_{dataset_id}"
            if ds_model_dir.exists() and ds_model_dir.is_dir():
                shutil.rmtree(ds_model_dir, ignore_errors=True)
        except Exception:
            logger.exception("Failed to clean dataset %s model dir", dataset_id)

    # ============== 校验 ==============

    @staticmethod
    async def assert_can_delete(
        db: AsyncSession,
        dataset: Dataset,
    ) -> None:
        """业务规则: 仅 draft 状态可删除

        非终态的数据集 (有进行中标注 / 训练) 拒绝删除, 避免脏数据
        """
        if dataset.status != DATASET_STATUS_DRAFT and dataset.status != DATASET_STATUS_DONE:
            raise HTTPException(
                status_code=409,
                detail=f"数据集状态为 {dataset.status!r}, 不允许删除. "
                       f"仅 draft / done 状态可删除.",
            )


__all__ = ["DatasetService"]
