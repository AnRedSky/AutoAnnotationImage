"""
Services Package
================
公共业务服务模块: 几何计算、AI 推理、文件存储等。
所有服务都设计为无状态 (除 ai_service 单例外), 便于单测。
"""
from app.services.ai_service import ai_service, AIService
from app.services.storage_service import storage_service, StorageService
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
    "ai_service", "AIService",
    "storage_service", "StorageService",
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
