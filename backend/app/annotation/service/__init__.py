"""
Annotation Services Package (Stage 2.4 填充)
==========================================

**v3.0.0 Stage 2.4 迁移**: 从 app/services/* 迁入 annotation 应用
**v3.0.0 审查修复**: AnnotationService 从 `app.tasks.service.annotation_service`
迁入本应用 (`app.annotation.service.annotation_service`), 解决跨应用
service 依赖. annotation 仍可调用 tasks 数据服务 (ImageService /
DetectionService / SegmentationService), 方向正确, 不构成循环.
"""
# Stage 3.0+ 阶段: 大服务已迁入当前应用, 业务逻辑下沉至本目录
from app.annotation.service.annotation_service import AnnotationService
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
    "AnnotationService",
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
