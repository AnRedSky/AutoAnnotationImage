"""
Tasks Workers Package — Celery 任务入口 (Stage 2.6 完整迁移)
============================================================

按任务类型拆分子模块:
- classification.py: 分类训练任务入口 - 原 app.tasks.workers.classification
- detection.py:      检测训练任务入口 - 原 app.tasks.workers.detection
- segmentation.py:   分割训练任务入口 - 原 app.tasks.workers.segmentation
- celery_app.py:     Celery 实例配置 - 原 app.tasks.workers.celery_app

每个 worker 文件薄 (委托 app.tasks.service.TrainingLifecycleService),
不写具体训练逻辑.

**v3.0.0 Stage 2.6**: 从 app.tasks.workers/ 整体迁移至此, app.tasks.workers/* 转为兼容垫片.
"""
# ---- 导入子模块: 让 @celery_app.task 装饰器在包被 import 时就被执行,
# 任务会注册到 celery_app.tasks. 这是 celery_app.py 的 include=[...] 参数之外的
# 第二道保险: 任何 import `app.tasks.workers` 或 `app.tasks.workers.celery_app` 的地方都会
# 顺带把 classification / detection / segmentation 三个 worker 也带进来.
from app.tasks.workers import (  # noqa: F401  (register celery tasks)
    classification,
    detection,
    segmentation,
)

__all__ = ["classification", "detection", "segmentation"]
