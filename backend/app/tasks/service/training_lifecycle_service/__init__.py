"""
TrainingLifecycleService — 训练任务生命周期服务 (Phase S5 拆分后入口)
====================================================================

**v3.0.0 Phase 5 新增**: 抽取 3 个 worker 文件 (tasks / detection_tasks /
segmentation_tasks) 的共同编排逻辑, 消除 ~600 行重复代码.

**v3.0.0 Phase S5 拆分**: 580 行单文件 → 4 个子模块 + 本 __init__.py 拼装层
- job.py    — TrainingJob 创建/重置/进度/历史/数据统计 (~230行)
- state.py  — 状态机 SUCCESS / FAILURE / PAUSED (~190行)
- model.py  — ModelVersion 创建 (~70行)
- celery.py — Celery state 推送 + sticky_meta 透传 (~60行)
- _compat.py — 兼容垫片 (旧 worker 用的模块级函数)

**职责总览**:
- TrainingJob 创建/重置/状态机推进 (PROGRESS/SUCCESS/FAILURE/PAUSED)
- 训练历史累积 (Redis + DB 双写)
- 数据集统计 sticky_meta 持久化
- ModelVersion 创建
- 失败清理 (ModelVersion 记录 + 磁盘 .pth 清理)

**与已有服务的关系**:
- TrainingService: 负责"启动" (start / submit) — 已经在 Phase 3 落地
- JobStateService: 负责"查询" (snapshot) — 已经在 Phase 3 落地
- TrainingLifecycleService: 负责"执行" (worker 内部的状态机) — Phase 5 新增

**向后兼容** (Phase S5 拆分后):
- 旧 import 路径 `from app.tasks.service.training_lifecycle_service import TrainingLifecycleService` ✅
- 旧 import 路径 `from app.tasks.service.training_lifecycle_service import _update_training_history, _persist_dataset_stats` ✅
- 旧调用形式 `TrainingLifecycleService.create_or_reset_job_sync(...)` ✅
- 内部实现从单文件 class 改为 "module-level functions + class 作为 namespace 包装"
"""
from __future__ import annotations

import logging

# 子模块导入 (必须在 class 之前)
from app.tasks.service.training_lifecycle_service.job import (
    create_or_reset_job,
    create_or_reset_job_sync,
    get_job_history,         # v3.6.4 新增
    get_job_history_sync,    # v3.6.4 新增
    update_job_progress,
    update_job_progress_sync,
    push_history,
    persist_dataset_stats,
    persist_dataset_stats_sync,
)
from app.tasks.service.training_lifecycle_service.state import (
    mark_success,
    mark_success_sync,
    mark_failure,
    mark_failure_sync,
    mark_paused,
    mark_paused_sync,
    mark_canceled,       # v3.5.0 新增
    mark_canceled_sync,  # v3.5.0 新增
)
from app.tasks.service.training_lifecycle_service.model import (
    create_model_version,
    create_model_version_sync,
)
from app.tasks.service.training_lifecycle_service.celery import (
    set_task_state,
    set_last_sticky_meta,
    get_last_sticky_meta,
)
from app.tasks.service.training_lifecycle_service._compat import (
    _update_training_history,
    _persist_dataset_stats,
)

logger = logging.getLogger(__name__)


# ============== 拼装 TrainingLifecycleService (作为方法 namespace) ==============
# Phase S5 设计: 子模块导出 module-level 函数, 本类用 staticmethod 包装保持 API 一致.
# 外部代码依然可以 `TrainingLifecycleService.mark_success(...)` 调用, 不感知拆分.

class TrainingLifecycleService:
    """训练任务生命周期服务 (无状态, 静态方法)

    所有方法都是 worker 可直接调用的同步 API (内部用 _run_async 切到事件循环).

    **Phase S5 后**: 类本身只做方法挂载, 实际方法定义在 4 个子模块:
    - job    → create_or_reset_job / get_job_history (v3.6.4) / update_job_progress / push_history / persist_dataset_stats
    - state  → mark_success / mark_failure / mark_paused / mark_canceled (v3.5.0)
    - model  → create_model_version
    - celery → set_task_state / set_last_sticky_meta / get_last_sticky_meta
    """

    # ============== 1. TrainingJob 创建/重置/进度/历史/数据统计 ==============
    create_or_reset_job = staticmethod(create_or_reset_job)
    create_or_reset_job_sync = staticmethod(create_or_reset_job_sync)
    get_job_history = staticmethod(get_job_history)              # v3.6.4 新增
    get_job_history_sync = staticmethod(get_job_history_sync)    # v3.6.4 新增
    update_job_progress = staticmethod(update_job_progress)
    update_job_progress_sync = staticmethod(update_job_progress_sync)
    push_history = staticmethod(push_history)
    persist_dataset_stats = staticmethod(persist_dataset_stats)
    persist_dataset_stats_sync = staticmethod(persist_dataset_stats_sync)

    # ============== 2. 状态机 SUCCESS / FAILURE / PAUSED / CANCELED ==============
    mark_success = staticmethod(mark_success)
    mark_success_sync = staticmethod(mark_success_sync)
    mark_failure = staticmethod(mark_failure)
    mark_failure_sync = staticmethod(mark_failure_sync)
    mark_paused = staticmethod(mark_paused)
    mark_paused_sync = staticmethod(mark_paused_sync)
    mark_canceled = staticmethod(mark_canceled)          # v3.5.0 新增
    mark_canceled_sync = staticmethod(mark_canceled_sync) # v3.5.0 新增

    # ============== 3. ModelVersion 创建 ==============
    create_model_version = staticmethod(create_model_version)
    create_model_version_sync = staticmethod(create_model_version_sync)

    # ============== 4. Celery state + sticky_meta ==============
    set_task_state = staticmethod(set_task_state)
    set_last_sticky_meta = staticmethod(set_last_sticky_meta)
    get_last_sticky_meta = staticmethod(get_last_sticky_meta)


__all__ = [
    "TrainingLifecycleService",
    # 兼容旧 import 路径 (Phase 5 引入)
    "_update_training_history",
    "_persist_dataset_stats",
]
