"""
training_lifecycle_service.celery — Celery state 推送 + sticky_meta 透传
====================================================================

**v3.0.0 Phase S5 拆分**: 从 training_lifecycle_service.py (580行) 抽离
**职责**:
- set_task_state — 统一 update_state (FAILURE 自动补 exc_type)
- set_last_sticky_meta / get_last_sticky_meta — 跨函数透传 sticky_meta
- _LAST_STICKY_META — 模块级 dict (worker 进程内单例, task_id 同时只跑一个)

**为什么独立**: 这两个职责与 TrainingJob/ModelVersion DB 写入无关,
只跟 Celery 状态推送和 worker 内存状态有关, 独立后便于:
- 测试时单独 mock set_task_state
- 排查问题时只关注 Celery 状态机而不涉及 DB schema
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.database.redis import redis_client  # noqa: F401  (兼容旧 re-export)

logger = logging.getLogger(__name__)


# ============== 跨 worker 共享的 sticky_meta ==============
# v2.5.28 引入: 失败路径 (_finish_failed_job) 也能拿到已计算的数据集统计
# 模块级 dict 跨函数共享, 简单够用 (每个 task_id 同时只有一个 worker 跑)
_LAST_STICKY_META: Dict[str, Dict[str, Any]] = {}


def set_task_state(celery_task: Any, state: str, meta: Dict[str, Any]) -> None:
    """统一的 Celery update_state, FAILURE 必须带 exc_type

    Args:
        celery_task: Celery task 实例 (self)
        state: PROGRESS / SUCCESS / FAILURE / REVOKED
        meta: 推送给前端的 meta dict
    """
    if state == "FAILURE" and "exc_type" not in meta:
        meta["exc_type"] = "UnknownError"
    try:
        celery_task.update_state(state=state, meta=meta)
    except Exception as e:
        logger.warning("set_task_state(%s) failed: %s", state, e)


def set_last_sticky_meta(task_id: str, sticky_meta: Dict[str, Any]) -> None:
    """记录最后一次的 sticky_meta (失败路径也能拿到)"""
    _LAST_STICKY_META[task_id] = dict(sticky_meta)


def get_last_sticky_meta(task_id: str) -> Dict[str, Any]:
    """读取上次记录的 sticky_meta"""
    return _LAST_STICKY_META.get(task_id, {})


__all__ = [
    "set_task_state",
    "set_last_sticky_meta",
    "get_last_sticky_meta",
    "_LAST_STICKY_META",
]
