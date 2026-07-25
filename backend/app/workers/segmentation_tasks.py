"""
兼容垫片 (Stage 2.6): segmentation_tasks
=========================================

**v3.0.0 Stage 2.6 迁移**: 分割 worker 已迁入 app.tasks.workers.segmentation,
本文件 re-export Celery 任务函数, 保持旧 import 路径可用.
"""
from app.tasks.workers.segmentation import (  # noqa: F401
    train_segmentation_task,
    auto_annotate_segmentation_task,
)


__all__ = [
    "train_segmentation_task",
    "auto_annotate_segmentation_task",
]
