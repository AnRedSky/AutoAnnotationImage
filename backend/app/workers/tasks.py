"""
兼容垫片 (Stage 2.6): tasks
============================

**v3.0.0 Stage 2.6 迁移**: 分类 worker 已迁入 app.tasks.workers.classification,
本文件 re-export Celery 任务函数 + 兼容垫片, 保持旧 import 路径可用.

注意: Celery task 注册基于 task name, 旧路径 (app.workers.tasks) 与新路径
(app.tasks.workers.classification) 不会冲突, 因为它们的 task 装饰器都绑定在
同一个 celery_app 实例上, Celery 通过 task 名称去重.
"""
# ---- 真实 task 函数 (重导出) ----
from app.tasks.workers.classification import (  # noqa: F401
    train_model_task,
    auto_annotate_task,
)

# ---- 兼容垫片: 委托给 TrainingLifecycleService ----
# 旧 detection/segmentation worker 通过 `from app.workers.tasks import _update_training_history`
# 调用. Phase 5 重构后这些函数已迁移到 TrainingLifecycleService, 这里保留导入转发避免破坏.
def _update_training_history(task_id: str, history: list) -> None:
    """兼容垫片: 委托给 TrainingLifecycleService.push_history"""
    from app.services import TrainingLifecycleService
    TrainingLifecycleService.push_history(task_id, history)


def _persist_dataset_stats(task_id: str, extra: dict) -> None:
    """兼容垫片: 委托给 TrainingLifecycleService.persist_dataset_stats_sync"""
    from app.services import TrainingLifecycleService
    TrainingLifecycleService.persist_dataset_stats_sync(task_id, extra)


__all__ = [
    "train_model_task",
    "auto_annotate_task",
    "_update_training_history",
    "_persist_dataset_stats",
]
