"""
兼容垫片 (Stage 2.3): AnnotationLog ORM Model
===========================================

**v3.0.0 Stage 2.3 迁移**: AnnotationLog 已迁入 app.tasks.model.annotation_log
"""
from app.tasks.model.annotation_log import *  # noqa: F401,F403
from app.tasks.model.annotation_log import AnnotationLog  # noqa: F401


__all__ = ["AnnotationLog"]
