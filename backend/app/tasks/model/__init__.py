"""
Tasks ORM Models — app/tasks/model/
===================================

**v3.0.0 Stage 2.3 新增**: tasks 应用的 ORM 模型集合.
包含: Dataset, Category, Image, AnnotationLog, ModelVersion, TrainingJob

**依赖**: app.common.base_model.Base
"""
from app.tasks.model.dataset import Dataset, DATASET_STATUS_VALUES
from app.tasks.model.category import Category
from app.tasks.model.image import (
    Image,
    IMAGE_STATUS_VALUES,
    IMAGE_STATUS_CONFIRMED,
)
from app.tasks.model.annotation_log import AnnotationLog
from app.tasks.model.model_version import ModelVersion
from app.tasks.model.training_job import (
    TrainingJob,
    TRAIN_STATE_VALUES,
    TRAIN_TERMINAL_STATES,
)

__all__ = [
    "Dataset",
    "Category",
    "Image",
    "AnnotationLog",
    "ModelVersion",
    "TrainingJob",
    # 状态枚举
    "DATASET_STATUS_VALUES",
    "IMAGE_STATUS_VALUES",
    "IMAGE_STATUS_CONFIRMED",
    "TRAIN_STATE_VALUES",
    "TRAIN_TERMINAL_STATES",
]
