"""
Tasks Services Package (Stage 2.4 完整迁移)
=========================================

**v3.0.0 Stage 2.4-2.8 迁移**: 从 app/services/* 迁入 tasks 应用

**当前状态 (Stage 2.8)**:
- 所有 8 个 tasks 业务服务已完整迁移到 app.tasks.service.*
- 3 个跨应用服务 (UserService/StatsService/AuthService) 已迁入各自应用
- 3 个工具服务已迁入 app.common.{ml,geometry,storage}
- app/services/ 目录即将删除

**跨应用工具** (BBox 几何计算) re-export 自 app.common.geometry.bbox_service.
"""
# Stage 2.4 已完整迁移 (含源码) - 跨应用服务
from app.admin.service.user_service import UserService
from app.admin.service.stats_service import StatsService
from app.auth.service.auth_service import AuthService

# Stage 2.4 已完整迁移 (含源码) - tasks 本应用服务
from app.tasks.service.dataset_service import DatasetService
from app.tasks.service.image_service import ImageService
from app.tasks.service.model_service import ModelService

# Stage 2.8 新迁移 - 业务编排服务
from app.tasks.service.job_state_service import JobStateService, JobStateSnapshot
from app.tasks.service.training_service import TrainingService
from app.tasks.service.training_lifecycle_service import TrainingLifecycleService
from app.tasks.service.training_data_service import TrainingDataService
from app.tasks.service.auto_annotate_service import (
    AutoAnnotateService, AutoAnnotateResult, ASYNC_THRESHOLD,
)
# v3.0.0 审查修复: AnnotationService 已迁入 annotation 应用
# 业务方应 `from app.annotation.service import AnnotationService`
# 此处不再 re-export 以避免循环导入 (annotation 反向 import tasks 数据服务)
from app.tasks.service.detection_service import DetectionService
from app.tasks.service.segmentation_service import SegmentationService

# 跨应用工具 (几何计算) — re-export 自 app.common.geometry.bbox_service
from app.common.geometry.bbox_service import (
    BBox,
    validate_normalized_bbox,
    normalized_to_pixels,
    pixels_to_normalized,
    iou,
    nms,
    class_wise_nms,
    bbox_from_dict,
    bbox_to_yolo_line,
    yolo_line_to_bbox,
)

__all__ = [
    # 跨应用服务
    "UserService", "StatsService", "AuthService",
    # tasks 本应用服务
    "DatasetService", "ImageService", "ModelService",
    # 业务编排服务 (Stage 2.8 迁移)
    "JobStateService", "JobStateSnapshot",
    "TrainingService",
    "TrainingLifecycleService",
    "TrainingDataService",
    "AutoAnnotateService", "AutoAnnotateResult", "ASYNC_THRESHOLD",
    # AnnotationService 已迁入 annotation 应用, 不再在此 re-export
    "DetectionService",
    "SegmentationService",
    # 跨应用工具
    "BBox",
    "validate_normalized_bbox",
    "normalized_to_pixels",
    "pixels_to_normalized",
    "iou",
    "nms",
    "class_wise_nms",
    "bbox_from_dict",
    "bbox_to_yolo_line",
    "yolo_line_to_bbox",
]
