"""
training_lifecycle_service._compat — 兼容垫片
==============================================

**v3.0.0 Phase S5 新增**: 旧 worker 代码 (Phase 5 重构前的 tasks.py) 使用模块级函数,
保留这两个包装函数避免破坏, 内部委托给 TrainingLifecycleService.

**保留函数**:
- `_update_training_history(task_id, history)` — 委托 push_history
- `_persist_dataset_stats(task_id, extra)` — 委托 persist_dataset_stats_sync

**未来计划**: Phase 5.6 清理 — 确认所有 worker 都不再使用模块级函数后删除本文件.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _update_training_history(task_id: str, history: list) -> None:
    """兼容垫片: 旧 tasks.py 模块级函数, 委托给 TrainingLifecycleService.push_history"""
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService
    TrainingLifecycleService.push_history(task_id, history)


def _persist_dataset_stats(task_id: str, extra: dict) -> None:
    """兼容垫片: 旧 tasks.py 模块级函数, 委托给 TrainingLifecycleService"""
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService
    TrainingLifecycleService.persist_dataset_stats_sync(task_id, extra)


__all__ = [
    "_update_training_history",
    "_persist_dataset_stats",
]
