"""
兼容垫片 (Stage 2.3): BBoxAnnotation ORM Model
============================================

**v3.0.0 Stage 2.3 迁移**: BBoxAnnotation 已迁入 app.annotation.model.bbox_annotation
"""
from app.annotation.model.bbox_annotation import *  # noqa: F401,F403
from app.annotation.model.bbox_annotation import BBoxAnnotation  # noqa: F401


__all__ = ["BBoxAnnotation"]
