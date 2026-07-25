"""
workers.segmentation Package — 图像分割 Celery 任务
====================================================

**v3.0.0 Phase S6 拆分**: 将原 workers/segmentation.py (372行) 拆为两个子模块
- train.py        — train_segmentation_task (DeepLabV3+ 训练)
- auto_annotate.py — auto_annotate_segmentation_task (用已训练模型批量预标注)

**对外接口 (保持完全向后兼容)**:
- `from app.tasks.workers.segmentation import train_segmentation_task` ✅
- `from app.tasks.workers.segmentation import auto_annotate_segmentation_task` ✅
- `from app.tasks.workers.segmentation import _finish_failed` ✅ (内部共享)

**Celery 字符串路径**:
- `app.tasks.workers.segmentation.train_segmentation_task` → 物理: segmentation.train
- `app.tasks.workers.segmentation.auto_annotate_segmentation_task` → 物理: segmentation.auto_annotate

注: 与 detection 不同, segmentation 的 task name 仍为 `...segmentation.train_segmentation_task`
(因为保留了旧函数名, Celery 通过 `bind=True` 装饰器 + 模块路径自动注册).
"""
from app.tasks.workers.segmentation.train import (
    train_segmentation_task,
    _finish_failed,
)
from app.tasks.workers.segmentation.auto_annotate import (
    auto_annotate_segmentation_task,
)

__all__ = [
    "train_segmentation_task",
    "auto_annotate_segmentation_task",
    # 内部共享失败处理 (Phase S6 抽离)
    "_finish_failed",
]
