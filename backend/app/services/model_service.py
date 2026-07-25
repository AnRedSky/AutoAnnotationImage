"""
兼容垫片 (Stage 2.4): ModelService
=================================

**v3.0.0 Stage 2.4 迁移**: ModelService 已迁入 app.tasks.service.model_service
"""
from app.tasks.service.model_service import *  # noqa: F401,F403
from app.tasks.service.model_service import ModelService  # noqa: F401


__all__ = ["ModelService"]
