"""
兼容垫片 (Stage 2.4): model_version_queries
==========================================

**v3.0.0 Stage 2.4 迁移**: model_version_queries 已迁入 app.tasks.repository.model_version_queries
"""
from app.tasks.repository.model_version_queries import *  # noqa: F401,F403
from app.tasks.repository.model_version_queries import (
    list_versions_by_dataset,
    get_active_model,
    get_version_by_id,
)  # noqa: F401


__all__ = [
    "list_versions_by_dataset",
    "get_active_model",
    "get_version_by_id",
]
