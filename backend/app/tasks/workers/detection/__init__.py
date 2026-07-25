"""
workers.detection Package — 目标检测 Celery 任务
================================================

**v3.0.0 Phase S4 拆分**: 将原 workers/detection.py (520行) 拆为两个子模块
- train.py        — train_detection_task (YOLOv8 训练)
- auto_annotate.py — auto_annotate_detection_task / auto_annotate_pretrained_task

**对外接口 (保持完全向后兼容)**:
- `from app.tasks.workers.detection import train_detection_task` ✅
- `from app.tasks.workers.detection import auto_annotate_detection_task` ✅
- `from app.tasks.workers.detection import auto_annotate_pretrained_task` ✅
- `from app.tasks.workers.detection import PREDEFINED_YOLO_MODELS` ✅

**Celery 字符串路径** (worker 任务名):
- `app.tasks.workers.detection:train_detection_task`
- `app.tasks.workers.detection:auto_annotate_detection_task`
- `app.tasks.workers.detection:auto_annotate_pretrained_task`

物理位置变化不影响字符串路径, 因为 Celery 通过 `bind=True` 装饰器在 import 时
自动注册 task 名称.
"""
from app.tasks.workers.detection.train import (
    train_detection_task,
    _finish_failed,
)
from app.tasks.workers.detection.auto_annotate import (
    auto_annotate_detection_task,
    auto_annotate_pretrained_task,
    PREDEFINED_YOLO_MODELS,
)

__all__ = [
    "train_detection_task",
    "auto_annotate_detection_task",
    "auto_annotate_pretrained_task",
    "PREDEFINED_YOLO_MODELS",
    # 内部工具 (worker 之间共享失败处理)
    "_finish_failed",
]
