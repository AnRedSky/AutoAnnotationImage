"""
Detection ML Package (v2.0.0 目标检测 / v3.0.0 Stage 2.6 重定位)
============================================================

封装 ultralytics YOLOv8 全链路:
- yolo_dataset: BBoxAnnotation → YOLO 训练目录
- yolo_train: YOLOv8 微调 (返回 best.pt + 指标)
- yolo_predict: 单图/批量推理 (输出归一化 bbox)

**v3.0.0 Stage 2.6 迁移**: 原 app.tasks.ml.detection 重定位至 app.tasks.ml.detection,
app.tasks.ml.detection 转为兼容垫片.

约定:
- ultralytics 全部 lazy import, 模块导入无需装 ultralytics
- 推理/训练失败抛 YoloTrainError / RuntimeError, 由 Celery 任务捕获
"""
from app.tasks.ml.detection.yolo_dataset import (
    export_yolo_dataset,
    build_class_index_map,
    split_train_val,
    annotations_to_yolo_lines,
)
from app.tasks.ml.detection.yolo_train import (
    train_yolo,
    YoloTrainError,
    cleanup_old_runs,
)
from app.tasks.ml.detection.yolo_predict import (
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
