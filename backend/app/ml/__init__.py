"""
兼容垫片 (Stage 2.6): ML Package
================================

**v3.0.0 Stage 2.6 迁移**: 原 app.ml 内容已迁入 app.tasks.ml,
本文件 re-export 保持旧 import 路径可用.

- app.ml.train        -> app.tasks.ml.classification
- app.ml.detection    -> app.tasks.ml.detection
- app.ml.segmentation -> app.tasks.ml.segmentation
"""
from app.tasks.ml import *  # noqa: F401,F403
from app.tasks.ml import (  # noqa: F401
    run_training,
    TrainingPaused,
    collect_device_info,
    select_device,
    ImageClassificationDataset,
    detection,
    segmentation,
)


__all__ = [
    "run_training",
    "TrainingPaused",
    "collect_device_info",
    "select_device",
    "ImageClassificationDataset",
    "detection",
    "segmentation",
]
