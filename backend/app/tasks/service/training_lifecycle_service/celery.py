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
from typing import Any, Dict, List, Optional

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


# ============== v3.6.8 HOTFIX: DB message 字段智能去重 ==============
# 背景: progress_cb 每 batch 调一次 set_task_state (1000 calls/epoch), 全部传 commit_message 会
#       把 DB UPDATE 频率拉到 1000+/epoch, 性能灾难.
# 解决: 增加二级去重缓存, 只有"msg 变化 / progress 1% / 5s 兜底 / 终态" 才真写 DB.
#       与 _LAST_LOG_SIG 平级, 独立 key 空间, 职责正交 (log 行 vs message 字段).
_LAST_COMMIT_MSG_SIG: Dict[str, tuple] = {}  # task_id -> (msg, progress, ts)

# 终态关键字 (触发强制 commit, 防止终态文案被 dedup 吃掉)
# 包含中英文: 兼容多语言 worker 日志
_TERMINAL_MSG_KEYWORDS = (
    "Training completed", "Canceled at", "Paused at",
    "训练完成", "已取消", "已暂停",
)


def _should_commit_message(
    task_id: Optional[str],
    msg: Optional[str],
    progress: Optional[float],
) -> bool:
    """v3.6.8 新增: 决定是否将 msg 真正写入 DB message 字段

    触发规则 (任一满足即返回 True):
    1. 终态关键字 (Training completed / Canceled at / Paused at) → 总是 commit
    2. msg 与上次不同 → commit
    3. progress 与上次变化 >= 1% → commit
    4. 首次调用 (无缓存) → commit
    5. 上次缓存后超过 5 秒 → commit (兜底, 防 msg 长期不变卡住)

    性能: 1000 calls/epoch → ~20-50 DB writes/epoch (msg 变化或 progress ≥ 1%)
    """
    if not task_id or not msg:
        return True
    if any(kw in msg for kw in _TERMINAL_MSG_KEYWORDS):
        return True

    last = _LAST_COMMIT_MSG_SIG.get(task_id)
    now_ts = datetime.utcnow().timestamp()
    if not last:
        return True
    last_msg, last_progress, last_ts = last
    if msg != last_msg:
        return True
    if progress is not None and last_progress is not None:
        try:
            if abs(float(progress) - float(last_progress)) >= 1.0:
                return True
        except (TypeError, ValueError):
            pass
    if (now_ts - last_ts) >= 5.0:
        return True
    return False


def _persist_log_line_sync(
    celery_task_id: Optional[str],
    line: str,
    *,
    commit_progress: Optional[float] = None,
    commit_message: Optional[str] = None,
    commit_current_epoch: Optional[int] = None,
    commit_history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """同步包装: 异步把一行日志写入 TrainingJob.log

    失败静默吞掉, 不影响 set_task_state 主流程.
    celery_task_id 为 None (极端情况) 时也静默跳过.
    commit_* 参数透传给 _persist_log_line_async, 用于合并写 (Phase T7 #8).
    """
    if not celery_task_id or not line:
        return
    if len(line) > _LOG_LINE_MAX_LEN:
        line = line[: _LOG_LINE_MAX_LEN - 3] + "..."
    try:
        _run_async(_persist_log_line_async(
            celery_task_id, line,
            commit_progress=commit_progress,
            commit_message=commit_message,
            commit_current_epoch=commit_current_epoch,
            commit_history=commit_history,
        ))
    except Exception as e:
        # 日志持久化是 best-effort, 不影响主流程
        logger.debug("persist_log_line(%s) skipped: %s", celery_task_id, e)


async def _persist_log_line_async(
    celery_task_id: str,
    line: str,
    *,
    commit_progress: Optional[float] = None,
    commit_message: Optional[str] = None,
    commit_current_epoch: Optional[int] = None,
    commit_history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """异步把一行日志写入 TrainingJob.log, 含 200 行截断

    v3.5.0 Phase T7 #8 优化: 合并"epoch 多次 DB write"
    - 原: epoch 结束调 set_task_state (写 log 行) + push_history (写 progress/history),
          = 2 次 commit / epoch
    - 优: 把 commit_progress / commit_message / commit_current_epoch / commit_history
          一并在 _persist_log_line_async 内 commit, = 1 次 commit / epoch
    - 传入 None 的字段不写, 调用方按需传
    """
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
            # v3.5.0 Phase T7 #8: 合并写 progress / message / current_epoch / history
            # 任一字段不为 None 才更新, 避免空值覆盖已有数据
            if commit_progress is not None:
                try:
                    job.progress = float(commit_progress)
                except (TypeError, ValueError):
                    pass
            if commit_message is not None:
                job.message = commit_message
            if commit_current_epoch is not None:
                job.current_epoch = int(commit_current_epoch)
            if commit_history is not None:
                job.history = list(commit_history)
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


def set_task_state(
    celery_task: Any,
    state: str,
    meta: Dict[str, Any],
    *,
    commit_progress: Optional[float] = None,
    commit_message: Optional[str] = None,
    commit_current_epoch: Optional[int] = None,
    commit_history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """统一的 Celery update_state + 后端权威日志持久化

    v3.0.0 Phase T 业务规则变更:
    - 在推 Celery state 的同时, 同步持久化一行到 TrainingJob.log
    - 解决 "已完成任务无日志" 的历史问题:
      之前日志只由前端 saveDetailLog 写, 已完成任务详情又不连 SSE, 永远空

    v3.5.0 Phase T7 #8: 合并 DB write
    - 传 commit_progress / commit_message / commit_current_epoch / commit_history 时,
      在 _persist_log_line_async 内一次性 commit (log + 这些字段),
      减少 epoch 结束时的 2 次 DB write → 1 次
    - 调用方 (epoch_cb) 必须先调 set_task_state 再调 push_history(commit_db=False)

    Args:
        celery_task: Celery task 实例 (self)
        state: PROGRESS / SUCCESS / FAILURE / REVOKED
        meta: 推送给前端的 meta dict
        commit_progress: 可选, 同步写 job.progress (epoch_cb 场景)
        commit_message: 可选, 同步写 job.message
        commit_current_epoch: 可选, 同步写 job.current_epoch
        commit_history: 可选, 同步写 job.history (整列表, 训练历史)
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
    # v3.6.8 HOTFIX: commit_message 走 _should_commit_message 智能去重
    # 目的: progress_cb 每 batch 调用 (1000 calls/epoch), 但 msg 变化/进度 1% 才真写 DB
    _commit_message_effective: Optional[str] = None
    if commit_message is not None:
        if _should_commit_message(task_id, commit_message, commit_progress):
            _commit_message_effective = commit_message
            _LAST_COMMIT_MSG_SIG[task_id] = (
                commit_message, commit_progress, datetime.utcnow().timestamp()
            )

    # v3.5.0 Phase T7 #8: 合并写 progress/message/current_epoch/history
    _persist_log_line_sync(
        task_id, line,
        commit_progress=commit_progress,
        commit_message=_commit_message_effective,  # 走 dedup 决策
        commit_current_epoch=commit_current_epoch,
        commit_history=commit_history,
    )
    # 更新签名缓存
    try:
        progress = float(meta.get("progress") or 0)
    except (TypeError, ValueError):
        progress = 0.0
    msg = (meta.get("msg") or meta.get("message") or "")[:200]
    _LAST_LOG_SIG[task_id] = (state, progress, msg, datetime.utcnow())
    # v3.5.0 Phase T7 方案 C: 写库后 publish, 通知 SSE 端点立即推送
    publish_job_update(task_id)


def set_last_sticky_meta(task_id: str, sticky_meta: Dict[str, Any]) -> None:
    """记录最后一次的 sticky_meta (失败路径也能拿到)"""
    _LAST_STICKY_META[task_id] = dict(sticky_meta)


def get_last_sticky_meta(task_id: str) -> Dict[str, Any]:
    """读取上次记录的 sticky_meta"""
    return _LAST_STICKY_META.get(task_id, {})


# ============== 训练任务状态事件发布 (v3.5.0 Phase T7 方案 C) ==============
# 背景: SSE 端点原来用 1Hz 轮询 + Redis 缓存 + db_version 去重, 在没有信息更新时
# 仍然每秒查一次缓存. 方案 C 改为事件驱动 + 1s 兜底:
#   - worker 写库后 publish 一条通知到 `job_state_channel:{task_id}`
#   - SSE 端点 subscribe 该频道, 收到事件后立即失效缓存 + 查 DB + 推送给前端
#   - 1s 内无事件 → 走兜底轮询 (防 publish 失败/网络丢包)
#
# 为什么不直接推 state?
#  跨进程 SSE 端点可能挂在不同 worker/gunicorn 实例, Redis Pub/Sub 是天然的
#  跨进程广播, 简单可靠. 实际 payload (progress/epoch 等) 由 SSE 端点自己查 DB
#  拿最新值, 避免事件丢消息后 UI 永远滞后.

JOB_UPDATE_CHANNEL_TEMPLATE = "job_state_channel:{task_id}"


def publish_job_update(task_id: Optional[str]) -> None:
    """向 Redis 发布一次 "该 task 有新状态" 通知 (SSE 端点会订阅并立即推送)

    失败静默吞掉 (best-effort, 不影响训练主流程):
    - Redis 不可达 → SSE 端点走 1s 兜底轮询, 不影响功能
    - 没有订阅者 → publish 是 no-op, 无副作用
    """
    if not task_id:
        return
    try:
        channel = JOB_UPDATE_CHANNEL_TEMPLATE.format(task_id=task_id)
        # payload 仅用作唤醒信号, 真实数据由 SSE 端点查 DB 获取
        # (避免事件乱序 / 漏消息导致 UI 永远滞后)
        redis_client.publish(channel, "1")
    except Exception as e:
        logger.debug("publish_job_update(%s) failed: %s", task_id, e)


__all__ = [
    "set_task_state",
    "set_last_sticky_meta",
    "get_last_sticky_meta",
    "_build_log_line",
    "publish_job_update",
    "JOB_UPDATE_CHANNEL_TEMPLATE",
    # v3.6.8 新增: 智能去重函数, 供测试用
    "_should_commit_message",
    "_LAST_COMMIT_MSG_SIG",
]
