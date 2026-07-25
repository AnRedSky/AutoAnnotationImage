"""
兼容垫片 (Stage 2.4): DatasetService
====================================

**v3.0.0 Stage 2.4 迁移**: DatasetService 已迁入 app.tasks.service.dataset_service
"""
from app.tasks.service.dataset_service import *  # noqa: F401,F403
from app.tasks.service.dataset_service import DatasetService  # noqa: F401


__all__ = ["DatasetService"]
