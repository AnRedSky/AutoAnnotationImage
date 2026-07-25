"""
app.common.geometry — 跨应用几何计算工具

包含:
- bbox_service: BBox 几何计算 / 校验 / NMS (原 app.common.geometry.bbox_service)

依赖方向: app.common.geometry 仅依赖标准库, 不依赖任何业务层.
"""
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
