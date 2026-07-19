"""ML Package (v2.0.0 多任务)

- train.py:                图像分类 (timm) 训练
- detection/yolo_dataset:  检测数据导出
- detection/yolo_train:    YOLOv8 训练
- detection/yolo_predict:  YOLOv8 推理
"""
from app.ml import detection  # noqa: F401  (注册 detection 子包)
