"""
兼容垫片 (Stage 2.6): detection_tasks
======================================

**v3.0.0 Stage 2.6 迁移**: 检测 worker 已迁入 app.tasks.workers.detection,
本文件 re-export Celery 任务函数, 保持旧 import 路径可用.
"""
from app.tasks.workers.detection import (  # noqa: F401
    train_detection_task,
    auto_annotate_detection_task,
    auto_annotate_pretrained_task,
    PREDEFINED_YOLO_MODELS,
)


__all__ = [
    "train_detection_task",
    "auto_annotate_detection_task",
    "auto_annotate_pretrained_task",
    "PREDEFINED_YOLO_MODELS",
]
