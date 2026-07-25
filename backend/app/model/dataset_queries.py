"""
兼容垫片 (Stage 2.4): dataset_queries
====================================

**v3.0.0 Stage 2.4 迁移**: dataset_queries 已迁入 app.tasks.repository.dataset_queries
"""
from app.tasks.repository.dataset_queries import *  # noqa: F401,F403
from app.tasks.repository.dataset_queries import (
    get_dataset_by_id,
    list_datasets_by_owner,
    list_datasets_by_task_type,
    count_datasets_by_owner,
    list_all_datasets,
)  # noqa: F401


__all__ = [
    "get_dataset_by_id",
    "list_datasets_by_owner",
    "list_datasets_by_task_type",
    "count_datasets_by_owner",
    "list_all_datasets",
]
