"""
兼容垫片 (Stage 2.3): Image ORM Model
====================================

**v3.0.0 Stage 2.3 迁移**: Image 已迁入 app.tasks.model.image
"""
from app.tasks.model.image import *  # noqa: F401,F403
from app.tasks.model.image import (
    Image,
    IMAGE_STATUS_VALUES,
    IMAGE_STATUS_CONFIRMED,
)  # noqa: F401


__all__ = ["Image", "IMAGE_STATUS_VALUES", "IMAGE_STATUS_CONFIRMED"]
