"""
兼容垫片 (Stage 2.3): ModelVersion ORM Model
==========================================

**v3.0.0 Stage 2.3 迁移**: ModelVersion 已迁入 app.tasks.model.model_version
"""
from app.tasks.model.model_version import *  # noqa: F401,F403
from app.tasks.model.model_version import ModelVersion  # noqa: F401


__all__ = ["ModelVersion"]
