"""Tasks Repository Package — 复杂查询 (Stage 2.4 填充)"""
from app.tasks.repository.dataset_queries import (
    get_dataset_by_id,
    list_datasets_by_owner,
    list_datasets_by_task_type,
    count_datasets_by_owner,
    list_all_datasets,
)
from app.tasks.repository.image_queries import (
    get_image_by_id,
    list_images_by_dataset,
    list_confirmed_images,
    list_pending_images,
    count_images_by_dataset,
    count_images_by_status,
)
from app.tasks.repository.model_version_queries import (
    list_versions_by_dataset,
    get_active_model,
    get_version_by_id,
)
from app.tasks.repository.training_queries import (
    get_training_job_by_id,
    get_training_job_by_celery_id,
    list_jobs_by_user,
    list_jobs_by_dataset,
    list_active_jobs,
    count_jobs_by_state,
)

__all__ = [
    # dataset
    "get_dataset_by_id",
    "list_datasets_by_owner",
    "list_datasets_by_task_type",
    "count_datasets_by_owner",
    "list_all_datasets",
    # image
    "get_image_by_id",
    "list_images_by_dataset",
    "list_confirmed_images",
    "list_pending_images",
    "count_images_by_dataset",
    "count_images_by_status",
    # model_version
    "list_versions_by_dataset",
    "get_active_model",
    "get_version_by_id",
    # training
    "get_training_job_by_id",
    "get_training_job_by_celery_id",
    "list_jobs_by_user",
    "list_jobs_by_dataset",
    "list_active_jobs",
    "count_jobs_by_state",
]

