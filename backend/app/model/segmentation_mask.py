"""
兼容垫片 (Stage 2.3): SegmentationMask ORM Model
==============================================

**v3.0.0 Stage 2.3 迁移**: SegmentationMask 已迁入 app.annotation.model.segmentation_mask
"""
from app.annotation.model.segmentation_mask import *  # noqa: F401,F403
from app.annotation.model.segmentation_mask import SegmentationMask  # noqa: F401


__all__ = ["SegmentationMask"]
