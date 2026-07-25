"""
Tasks ML Package — 机器学习业务逻辑 (Stage 2.6 完整迁移)
=========================================================

按任务类型拆分子模块:
- classification.py: 图像分类 (timm) - 原 app.ml.train
- detection/:        目标检测 (ultralytics YOLO) - 原 app.ml.detection
- segmentation/:     图像分割 (torchvision / SMP) - 原 app.ml.segmentation

**v3.0.0 Stage 2.6**: 从 app.ml/ 整体迁移至此, app.ml/* 转为兼容垫片.
"""
from app.tasks.ml.classification import (
    run_training,
    TrainingPaused,
    collect_device_info,
    select_device,
    ImageClassificationDataset,
)
from app.tasks.ml import detection, segmentation

__all__ = [
    # 分类
    "run_training",
    "TrainingPaused",
    "collect_device_info",
    "select_device",
    "ImageClassificationDataset",
    # 子包
    "detection",
    "segmentation",
]
