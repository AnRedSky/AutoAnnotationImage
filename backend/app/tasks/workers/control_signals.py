"""
control_signals - 训练任务暂停/取消信号的统一读写入口 (v3.5.0)
================================================================

设计背景:
  旧 cancel_training_job 走 Celery revoke(terminate=True) + SIGTERM 路径,
  但 .env: CELERY_WORKER_POOL=threads 用的线程池不支持 kill_job,
  取消按钮不生效. 修复方案: 把"取消"也走业务级 Redis signal,
  与"暂停"共用一套机制.

设计要点:
  1) pause 标志: train:pause:{task_id}   (TTL 3600s)
  2) cancel 标志: train:cancel:{task_id} (TTL 3600s)
  3) pause_check 返回 SignalAction 枚举 (CONTINUE/PAUSE/CANCEL)
  4) cancel 优先级 > pause (cancel 写后 pause 写不影响 cancel 行为)
  5) 全部 Redis 操作 try/except 包裹, 失败不抛 (业务信号不能阻塞主训练流程)
  6) Redis 客户端是同步的 (app.database.redis.redis_client = redis.Redis(...))

替换关系:
  - app/tasks/workers/classification.py 旧的 inline pause_check (line 158-163) 用 make_legacy_pause_check
  - 三个 worker 共用本模块, 避免代码重复

并发安全:
  - Redis key 含 task_id (UUID) 天然隔离不同任务
  - Redis GET / SETEX 都是原子操作, 多 worker 进程/线程并发安全
  - 多次 SETEX 写同一 key 幂等 (覆盖值)
"""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("app.tasks.workers.control_signals")


# ============== SignalAction 枚举 ==============

class SignalAction(str, Enum):
    """训练信号动作枚举 (worker pause_check 返回值)

    - CONTINUE: 没有信号, 继续训练
    - PAUSE:    用户请求暂停, worker 抛 TrainingPaused
    - CANCEL:   用户请求取消, worker 抛 TaskCanceled
    """
    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    CANCEL = "CANCEL"


# ============== TaskCanceled 异常 ==============

class TaskCanceled(Exception):
    """训练被用户主动取消 — 由 pause_check 抛出, worker 捕获后写 CANCELED 状态

    区别于 TrainingPaused:
    - TrainingPaused: 可恢复 (DB PAUSED, 用户点"继续" → 复用 job)
    - TaskCanceled:   不可恢复 (DB CANCELED, 用户点"再训练" → 新 job)

    Attributes:
        epoch: 取消时正在跑的 epoch (1-based)
        total_epochs: 总 epoch 数
        reason: 取消原因 (默认 "user_cancel", 预留扩展 OOM/timeout)
    """
    def __init__(self, epoch: int, total_epochs: int, reason: str = "user_cancel"):
        self.epoch = epoch
        self.total_epochs = total_epochs
        self.reason = reason
        super().__init__(f"Canceled at epoch {epoch}/{total_epochs} ({reason})")


# ============== Redis Key 命名 ==============

def _key_pause(task_id: str) -> str:
    return f"train:pause:{task_id}"


def _key_cancel(task_id: str) -> str:
    return f"train:cancel:{task_id}"


# ============== Redis 客户端 (同步) ==============

def _redis_or_none():
    """获取 redis 客户端, 失败返回 None (不抛).

    业务信号不能阻塞主训练流程, 这里所有异常都吞掉.
    """
    try:
        from app.database.redis import redis_client
        return redis_client
    except Exception as e:
        logger.warning(f"control_signals: redis 不可用: {e!r}")
        return None


# ============== API 端调用: 写信号 ==============

def request_pause(task_id: str, ttl: int = 3600) -> bool:
    """API 端调: 标记任务为暂停请求.

    Returns:
        True  - Redis 写入成功
        False - Redis 不可用或写入失败 (调用方不应强失败)
    """
    if not task_id:
        return False
    rc = _redis_or_none()
    if rc is None:
        return False
    try:
        rc.setex(_key_pause(task_id), ttl, str(int(time.time())))
        return True
    except Exception as e:
        logger.warning(f"request_pause 失败: {e!r}")
        return False


def request_cancel(task_id: str, ttl: int = 3600) -> bool:
    """API 端调: 标记任务为取消请求.

    Returns:
        True  - Redis 写入成功
        False - Redis 不可用或写入失败
    """
    if not task_id:
        return False
    rc = _redis_or_none()
    if rc is None:
        return False
    try:
        rc.setex(_key_cancel(task_id), ttl, str(int(time.time())))
        return True
    except Exception as e:
        logger.warning(f"request_cancel 失败: {e!r}")
        return False


# ============== Worker 端调用: 清信号 ==============

def clear_pause(task_id: str) -> None:
    """Worker 端调: 删除 pause 标志 (任务启动/完成后清理)."""
    if not task_id:
        return
    rc = _redis_or_none()
    if rc is None:
        return
    try:
        rc.delete(_key_pause(task_id))
    except Exception:
        pass


def clear_cancel(task_id: str) -> None:
    """Worker 端调: 删除 cancel 标志 (任务完成后清理)."""
    if not task_id:
        return
    rc = _redis_or_none()
    if rc is None:
        return
    try:
        rc.delete(_key_cancel(task_id))
    except Exception:
        pass


def clear_all(task_id: str) -> None:
    """Worker 端调: 任务启动时清掉历史残留标志 (避免误判).

    典型调用: 训练任务入口的 try 块前
        clear_all(task_id)  # 防止上次异常退出时残留的 pause/cancel
    """
    clear_pause(task_id)
    clear_cancel(task_id)


# ============== 工厂: 生成 pause_check 回调 ==============

def make_pause_check(task_id: str) -> Callable[[], SignalAction]:
    """工厂: 生成 worker 在每个 epoch 调用的检查函数 (SignalAction 版本).

    使用场景: ML 层训练函数 (run_training / train_yolo / train_segmentation) 接受
        pause_check: Optional[Callable[[], SignalAction]] = None
    参数, 内部每个 epoch 调一次. 返回值:
        - SignalAction.CONTINUE: 继续训练
        - SignalAction.PAUSE:    用户请求暂停 → 抛 TrainingPaused
        - SignalAction.CANCEL:   用户请求取消 → 抛 TaskCanceled

    决策表:
        | cancel key | pause key | 返回值    |
        |------------|-----------|-----------|
        | 存在       | 任意      | CANCEL    |
        | 不存在     | 存在      | PAUSE     |
        | 不存在     | 不存在    | CONTINUE  |
    """
    _key_p = _key_pause(task_id)
    _key_c = _key_cancel(task_id)

    def _check() -> SignalAction:
        rc = _redis_or_none()
        if rc is None:
            # Redis 不可用 → 视为无信号, 优雅降级继续训练
            return SignalAction.CONTINUE
        try:
            # cancel 优先 (语义: 一旦取消, 暂停不能阻止退出)
            if rc.get(_key_c) is not None:
                return SignalAction.CANCEL
            if rc.get(_key_p) is not None:
                return SignalAction.PAUSE
        except Exception as e:
            logger.warning(f"pause_check 读 redis 失败: {e!r}")
        return SignalAction.CONTINUE

    return _check


def make_legacy_pause_check(task_id: str) -> Callable[[], bool]:
    """工厂: 生成 bool 形式 pause_check (兼容 classification 旧调用).

    历史原因: classification.run_training 旧签名是
        pause_check: Optional[Callable[[], bool]] = None
    返回 True 表示"检测到 pause 信号". 为避免破坏 ML 层签名, 提供 bool 适配器.

    注意: 此适配器把 CANCEL 也视作"应停止" (返回 True). 旧代码捕获到 True
    后抛 TrainingPaused, 而新代码应在 worker 入口用 make_pause_check 区分
    CANCEL vs PAUSE.

    推荐: 新代码直接用 make_pause_check; 仅做兼容时用本函数.
    """
    check = make_pause_check(task_id)
    def _bool() -> bool:
        action = check()
        return action in (SignalAction.PAUSE, SignalAction.CANCEL)
    return _bool


# ============== 对外导出 ==============

__all__ = [
    "SignalAction",
    "TaskCanceled",
    "request_pause",
    "request_cancel",
    "clear_pause",
    "clear_cancel",
    "clear_all",
    "make_pause_check",
    "make_legacy_pause_check",
]
