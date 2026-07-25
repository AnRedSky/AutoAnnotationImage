"""
Annotation ORM Models — app/annotation/model/
============================================

**v3.0.0 Stage 2.3 新增**: annotation 应用的 ORM 模型集合.
包含: BBoxAnnotation (目标检测), SegmentationMask (图像分割)

**依赖**: app.common.base_model.Base
"""
from app.annotation.model.bbox_annotation import BBoxAnnotation
from app.annotation.model.segmentation_mask import SegmentationMask

__all__ = ["BBoxAnnotation", "SegmentationMask"]
