"""
Tasks Services Package (Stage 2.4 填充)
=======================================

**v3.0.0 Stage 2.4 迁移**: 从 app/services/* 迁入 tasks 应用

**重要**: 本 __init__.py 只 re-export 实际位于 app.tasks.service.* 的服务.
未完成迁移的服务 (training 等) 仍位于 app.services.*, 业务代码应继续
import from app.services.<name> 直到 Stage 2.6 完成.
"""
# Stage 2.4 已完整迁移 (含源码) - 跨应用服务
from app.admin.service.user_service import UserService
from app.admin.service.stats_service import StatsService
from app.auth.service.auth_service import AuthService

# Stage 2.4 已完整迁移 (含源码) - tasks 本应用服务
from app.tasks.service.dataset_service import DatasetService
from app.tasks.service.image_service import ImageService
from app.tasks.service.model_service import ModelService

# Re-export 工具函数 (来自 app.services.bbox_service, 无 cycle)
from app.services.bbox_service import (
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
    # Stage 2.4 完整迁移
    "UserService", "StatsService", "AuthService",
    "DatasetService", "ImageService", "ModelService",
    # 工具
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
