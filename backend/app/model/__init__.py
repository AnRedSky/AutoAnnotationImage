"""
Model Package (Data Layer)
==========================

所有 ORM 模型 + 查询函数 + Active Record 业务方法。

v3.0.0 Phase 2 新增 (从 app.models 迁移, Phase 5.6 已彻底删除兼容垫片):
- 9 个 ORM 模型 (含业务方法)
- 9 个 *_queries.py 查询函数文件
- __init__.py 统一导出

v3.0.0 Phase 5.6 更新:
- 兼容垫片 `app/models/` 已删除, 新代码统一 `from app.model.X import Y`
- 兼容垫片 `app/core/celery_utils.py` 已删除, 新代码统一 `from app.utils.async_helpers import ...`

依赖方向: model/ 仅依赖 app.model.base + sqlalchemy, 不依赖 app.services/api/workers.
"""
from app.model.user import User
from app.model.dataset import Dataset
from app.model.category import Category
from app.model.image import Image, IMAGE_STATUS_VALUES, IMAGE_STATUS_CONFIRMED
from app.model.annotation_log import AnnotationLog
from app.model.model_version import ModelVersion
from app.model.training_job import (
    TrainingJob,
    TRAIN_STATE_VALUES,
    TRAIN_TERMINAL_STATES,
)
from app.model.bbox_annotation import BBoxAnnotation
from app.model.segmentation_mask import SegmentationMask


__all__ = [
    "User",
    "Dataset",
    "Category",
    "Image",
    "AnnotationLog",
    "ModelVersion",
    "TrainingJob",
    "BBoxAnnotation",
    "SegmentationMask",
    # 状态枚举
    "IMAGE_STATUS_VALUES",
    "IMAGE_STATUS_CONFIRMED",
    "TRAIN_STATE_VALUES",
    "TRAIN_TERMINAL_STATES",
]
