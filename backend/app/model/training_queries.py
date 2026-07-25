"""
兼容垫片 (Stage 2.4): training_queries
====================================

**v3.0.0 Stage 2.4 迁移**: training_queries 已迁入 app.tasks.repository.training_queries
"""
from app.tasks.repository.training_queries import *  # noqa: F401,F403
from app.tasks.repository.training_queries import (
    get_training_job_by_id,
    get_training_job_by_celery_id,
    list_jobs_by_user,
    list_jobs_by_dataset,
    list_active_jobs,
    count_jobs_by_state,
)  # noqa: F401


__all__ = [
    "get_training_job_by_id",
    "get_training_job_by_celery_id",
    "list_jobs_by_user",
    "list_jobs_by_dataset",
    "list_active_jobs",
    "count_jobs_by_state",
]
