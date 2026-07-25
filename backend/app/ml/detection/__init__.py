"""
兼容垫片 (Stage 2.6): detection ML 子包
=========================================

**v3.0.0 Stage 2.6 迁移**: 原 app.ml.detection 内容已迁入 app.tasks.ml.detection,
本文件 re-export 整包, 保持旧 import 路径可用.
"""
from app.tasks.ml.detection import *  # noqa: F401,F403
from app.tasks.ml.detection import (  # noqa: F401
    export_yolo_dataset,
    build_class_index_map,
    split_train_val,
    annotations_to_yolo_lines,
    train_yolo,
    YoloTrainError,
    cleanup_old_runs,
    predict_yolo,
    predict_image_grouped,
    YoloBox,
)


__all__ = [
    "export_yolo_dataset",
    "build_class_index_map",
    "split_train_val",
    "annotations_to_yolo_lines",
    "train_yolo",
    "YoloTrainError",
    "cleanup_old_runs",
    "predict_yolo",
    "predict_image_grouped",
    "YoloBox",
]
