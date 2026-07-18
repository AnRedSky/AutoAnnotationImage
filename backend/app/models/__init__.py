"""
Models Package
==============
Import all models so SQLAlchemy can create tables properly.
"""
from app.models.user import User
from app.models.dataset import Dataset
from app.models.category import Category
from app.models.image import Image
from app.models.annotation_log import AnnotationLog
from app.models.model_version import ModelVersion
from app.models.training_job import TrainingJob
# v2.0.0: 检测 / 分割
from app.models.bbox_annotation import BBoxAnnotation
from app.models.segmentation_mask import SegmentationMask

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
]
