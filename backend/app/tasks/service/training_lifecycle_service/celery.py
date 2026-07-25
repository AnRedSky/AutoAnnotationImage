"""
training_lifecycle_service.celery — Celery state 推送 + sticky_meta 透传
====================================================================

**v3.0.0 Phase S5 拆分**: 从 training_lifecycle_service.py (580行) 抽离
**职责**:
- set_task_state — 统一 update_state (FAILURE 自动补 exc_type)
- set_last_sticky_meta / get_last_sticky_meta — 跨函数透传 sticky_meta
- _LAST_STICKY_META — 模块级 dict (worker 进程内单例, task_id 同时只跑一个)

**v3.0.0 业务规则变更 (Phase T)**:
- set_task_state 在推 Celery state 的同时, 同步持久化一行日志到 TrainingJob.log
- 这是后端权威日志入口, 不依赖任何前端 SSE 订阅
- 历史问题: 之前日志持久化只由前端的 saveDetailLog (SSE 收到推送时调) 负责,
  已完成任务的详情页又不连 SSE, 导致 "训练时没人订阅 → 日志永远空"
- 前端 saveDetailLog 降级为 best-effort 冗余, 主要用途已转移
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from app.database.redis import redis_client  # noqa: F401  (兼容旧 re-export)
from app.utils.async_helpers import run_async_in_worker as _run_async

logger = logging.getLogger(__name__)


# ============== 跨 worker 共享的 sticky_meta ==============
# v2.5.28 引入: 失败路径 (_finish_failed_job) 也能拿到已计算的数据集统计
# 模块级 dict 跨函数共享, 简单够用 (每个 task_id 同时只有一个 worker 跑)
_LAST_STICKY_META: Dict[str, Dict[str, Any]] = {}


def _build_log_line(state: str, meta: Dict[str, Any]) -> str:
    """把 state + 关键 meta 拼成一行日志 (前端展示用, 单行不超过 2KB)

    格式: [HH:MM:SS] state=PROGRESS progress=42.5% epoch=8/20 msg=...
    """
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    progress = meta.get("progress")
    epoch = meta.get("epoch")
    total = meta.get("total_epochs")
    msg = meta.get("msg") or meta.get("message") or ""
    # 截断 msg 到 500 字, 避免 2KB 行限制
    if isinstance(msg, str) and len(msg) > 500:
        msg = msg[:497] + "..."
    parts = [f"state={state}"]
    if progress is not None:
        try:
            parts.append(f"progress={float(progress):.1f}%")
        except (TypeError, ValueError):
            pass
    if epoch is not None or total is not None:
        parts.append(f"epoch={epoch if epoch is not None else '-'}/{total if total is not None else '-'}")
    if msg:
        parts.append(f"msg={msg}")
    return f"[{ts}] " + " ".join(parts)


# ============== 训练日志持久化 (worker 主动写) ==============
# v3.0.0 Phase T: 后端权威日志入口, 不依赖任何前端 SSE 订阅
# 设计:
# 1) set_task_state 被调用时, 同步触发一次 DB 异步写入
# 2) 用签名 (state + progress + msg 摘要) 去重, 避免每帧重复
# 3) 写入失败静默吞掉, 不影响 Celery state 推送主流程
# 4) 前端 saveDetailLog 保留为 best-effort 冗余 (用户手动刷新时仍可用)

_LOG_LINE_MAX_LEN = 2048  # 与 API /log 端点一致
_LOG_MAX_LINES = 200      # 与 TrainingJob.LOG_MAX_LINES 一致 (这里硬编码兜底)


# 模块级签名去重缓存 (worker 进程内, task_id 维度)
# key = celery_task_id, value = (state, progress_signature, msg_signature, last_line_ts)
_LAST_LOG_SIG: Dict[str, tuple] = {}


def _persist_log_line_sync(celery_task_id: Optional[str], line: str) -> None:
    """同步包装: 异步把一行日志写入 TrainingJob.log

    失败静默吞掉, 不影响 set_task_state 主流程.
    celery_task_id 为 None (极端情况) 时也静默跳过.
    """
    if not celery_task_id or not line:
        return
    if len(line) > _LOG_LINE_MAX_LEN:
        line = line[: _LOG_LINE_MAX_LEN - 3] + "..."
    try:
        _run_async(_persist_log_line_async(celery_task_id, line))
    except Exception as e:
        # 日志持久化是 best-effort, 不影响主流程
        logger.debug("persist_log_line(%s) skipped: %s", celery_task_id, e)


async def _persist_log_line_async(celery_task_id: str, line: str) -> None:
    """异步把一行日志写入 TrainingJob.log, 含 200 行截断"""
    try:
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.tasks.model.training_job import TrainingJob
    except Exception:
        return
    try:
        async with AsyncSessionLocal() as db:
            job = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == celery_task_id)
            )).scalar_one_or_none()
            if not job:
                return
            log = list(job.log) if isinstance(job.log, list) else []
            log.append(line)
            # 截断头部保留尾部 200 行
            if len(log) > _LOG_MAX_LINES:
                log = log[-_LOG_MAX_LINES:]
            job.log = log
            await db.commit()
    except Exception as e:
        logger.debug("persist_log_line_async(%s) DB write failed: %s", celery_task_id, e)


def _should_persist(state: str, meta: Dict[str, Any], task_id: str) -> bool:
    """签名去重: state 变化 / progress 变化 >=1% / msg 变化时才持久化

    终态 (SUCCESS/FAILURE/REVOKED) 总是持久化 (状态切换重要事件)
    """
    if state in ("SUCCESS", "FAILURE", "REVOKED"):
        return True
    try:
        progress = float(meta.get("progress") or 0)
    except (TypeError, ValueError):
        progress = 0.0
    msg = (meta.get("msg") or meta.get("message") or "")[:200]

    last = _LAST_LOG_SIG.get(task_id)
    if not last:
        return True
    last_state, last_progress, last_msg, _ = last
    if state != last_state:
        return True
    # progress 变化 >= 1% 才记
    if abs(progress - last_progress) >= 1.0:
        return True
    if msg != last_msg:
        return True
    return False


def set_task_state(celery_task: Any, state: str, meta: Dict[str, Any]) -> None:
    """统一的 Celery update_state + 后端权威日志持久化

    v3.0.0 Phase T 业务规则变更:
    - 在推 Celery state 的同时, 同步持久化一行到 TrainingJob.log
    - 解决 "已完成任务无日志" 的历史问题:
      之前日志只由前端 saveDetailLog 写, 已完成任务详情又不连 SSE, 永远空

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
        return

    # ---- 后端权威日志持久化 (与 Celery 推送解耦, 不影响主流程) ----
    task_id = None
    try:
        task_id = celery_task.request.id  # celery_task_id (UUID)
    except Exception:
        task_id = None
    if not task_id:
        return

    if not _should_persist(state, meta, task_id):
        return

    line = _build_log_line(state, meta)
    _persist_log_line_sync(task_id, line)
    # 更新签名缓存
    try:
        progress = float(meta.get("progress") or 0)
    except (TypeError, ValueError):
        progress = 0.0
    msg = (meta.get("msg") or meta.get("message") or "")[:200]
    _LAST_LOG_SIG[task_id] = (state, progress, msg, datetime.utcnow())


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
    "_build_log_line",
]
