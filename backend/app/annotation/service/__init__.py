"""
Annotation Services Package (Stage 2.4 填充)
==========================================

**v3.0.0 Stage 2.4 迁移**: 从 app/services/* 迁入 annotation 应用
"""
# Stage 2.4+ 阶段: 大服务仍指向 app.services.* (作为过渡)
from app.services.annotation_service import AnnotationService
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
