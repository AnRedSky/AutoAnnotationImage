"""
兼容垫片 (Stage 2.4): ImageService
================================

**v3.0.0 Stage 2.4 迁移**: ImageService 已迁入 app.tasks.service.image_service
"""
from app.tasks.service.image_service import *  # noqa: F401,F403
from app.tasks.service.image_service import ImageService  # noqa: F401


__all__ = ["ImageService"]
