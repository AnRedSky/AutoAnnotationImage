"""
兼容垫片 (Stage 2.6): train.py
===============================

**v3.0.0 Stage 2.6 迁移**: 训练函数已迁入 app.tasks.ml.classification,
本文件 re-export 保持旧 import 路径可用.
"""
from app.tasks.ml.classification import *  # noqa: F401,F403
from app.tasks.ml.classification import (  # noqa: F401
    run_training,
    TrainingPaused,
    collect_device_info,
    select_device,
    ImageClassificationDataset,
)


__all__ = [
    "run_training",
    "TrainingPaused",
    "collect_device_info",
    "select_device",
    "ImageClassificationDataset",
]
