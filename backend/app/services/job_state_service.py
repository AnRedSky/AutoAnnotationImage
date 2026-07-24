"""
JobStateService — 训练任务状态单一真相源
=====================================

**核心问题 (v3.0.0 修复)**:
训练任务状态曾分散在 4 处:
1. Celery AsyncResult.state (Redis broker)
2. Celery AsyncResult.info (Redis, 含 progress/msg meta)
3. TrainingJob.state/progress/message (MySQL)
4. 前端轮询本地 state (React useState)

导致问题:
- Celery SUCCESS 状态 result.info 是 task 返回值, 不含 progress 字段
  → UI 显示 0% 即使 DB 是 100%
- worker 崩溃但 DB 已写 → Celery 退化为 PENDING, DB 是 FAILURE
  → 4 处真相不一致
- Redis result 过期 (默认 1h) → Celery 不可查, 只能查 DB

**解决方案**:
- DB (TrainingJob) 是权威, Celery 是辅助
- 状态查询统一走本服务, 内部自动: Celery 拿 raw → DB 兜底 → 合并
- 所有状态机转移都走本服务 (transition_to_state), 业务方法只负责修改字段

**API 接入**:
```python
# 旧: 直接调 Celery + DB
state = AsyncResult(task_id).state
row = (await db.execute(...)).scalar_one_or_none()

# 新: 一行调用
snapshot = await JobStateService.get_snapshot(task_id, db)
```

v3.0.0 Phase 3 新增
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from celery.result import AsyncResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_utils import check_celery_available
from app.core.redis_client import redis_client
from app.database import AsyncSessionLocal
from app.model.training_job import (
    TrainingJob,
    TRAIN_STATE_PENDING,
    TRAIN_STATE_PROGRESS,
    TRAIN_STATE_SUCCESS,
    TRAIN_STATE_FAILURE,
    TRAIN_STATE_REVOKED,
)


# Re-export for callers that expect to find this from job_state_service
def _get_celery_app_safe():
    """安全导入 celery_app, 避免循环 import"""
    try:
        from app.workers.celery_app import celery_app
        return celery_app
    except ImportError:
        return None


# Celery 终态集合
_CELERY_TERMINAL = frozenset((TRAIN_STATE_SUCCESS, TRAIN_STATE_FAILURE, TRAIN_STATE_REVOKED))


@dataclass
class JobStateSnapshot:
    """训练任务状态快照 (前端轮询/SSE 统一返回结构)

    字段说明:
    - state: 权威 state (DB 优先, Celery 兜底)
    - progress: 0-100
    - message: 状态描述 (DB message 优先, info.msg 兜底)
    - current_epoch / total_epochs: 当前/总 epoch (历史曲线用)
    - started_at / finished_at: 时间戳 (前端展示)
    - error: 错误信息 (仅 FAILURE 有)
    - history: 训练历史 (list of dict, 来自 DB JSON)
    - source: 'db' / 'celery' / 'merged' (调试用)
    """
    task_id: str
    state: str
    progress: float
    message: str
    current_epoch: Optional[int] = None
    total_epochs: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None
    source: str = "db"  # 'db' / 'celery' / 'merged'


class JobStateService:
    """训练任务状态服务 (无状态, 静态方法)

    职责:
    1. get_snapshot: 合并 Celery + DB 拿到权威状态
    2. transition_to_state: 统一状态机转移入口
    3. refresh_state: 主动刷新某个 job 的 DB 状态
    """

    # ============== 状态查询 (Celery + DB 合并) ==============

    @staticmethod
    async def get_snapshot(
        task_id: str,
        db: AsyncSession,
        *,
        include_history: bool = False,
    ) -> JobStateSnapshot:
        """获取任务状态快照 (DB 优先 + Celery 兜底)

        合并规则 (严格按以下顺序):
        1. 永远先查 DB 拿权威 state (若存在)
        2. 若 DB 不存在, 用 Celery state
        3. 终态 (SUCCESS/FAILURE/REVOKED) 一律以 DB 为准
        4. PROGRESS 状态: progress 来自 Celery.info (worker 实时推),
           message 来自 DB (worker 落库更可靠)
        5. 找不到任何记录 → PENDING + progress=0
        """
        # ---- 1) 查 Celery ----
        celery_state = TRAIN_STATE_PENDING
        celery_info: Dict[str, Any] = {}
        try:
            result = AsyncResult(task_id)
            try:
                celery_state = result.state or TRAIN_STATE_PENDING
            except Exception:
                celery_state = TRAIN_STATE_PENDING
            try:
                raw_info = result.info
                if isinstance(raw_info, dict):
                    celery_info = raw_info
            except Exception:
                celery_info = {}
        except Exception:
            # Celery 不可达 (Redis 断开) → 当作 PENDING, 后续靠 DB 兜底
            celery_state = TRAIN_STATE_PENDING
            celery_info = {}

        # ---- 2) 查 DB ----
        db_row: Optional[TrainingJob] = None
        try:
            db_row = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
        except Exception:
            db_row = None

        # ---- 3) 合并 ----
        if db_row is None:
            # 无 DB 记录 → 全靠 Celery
            return JobStateSnapshot(
                task_id=task_id,
                state=celery_state,
                progress=float(celery_info.get("progress", 0.0)),
                message=celery_info.get("msg") or celery_info.get("message", "") or "",
                current_epoch=celery_info.get("epoch"),
                total_epochs=celery_info.get("total_epochs"),
                source="celery",
            )

        # DB 有记录 → DB 优先
        state = db_row.state or celery_state
        # 终态永远以 DB 为准
        if state not in _CELERY_TERMINAL and celery_state in _CELERY_TERMINAL:
            # 边缘情况: Celery 终态但 DB 还没切 (短暂) → 跟 Celery
            state = celery_state

        # progress / message 合并
        if state in _CELERY_TERMINAL:
            # 终态: DB 权威
            progress = float(db_row.progress or 0.0)
            message = db_row.message or (db_row.error[:200] if db_row.error else "")
        else:
            # PROGRESS: progress 来自 Celery (实时), message 来自 DB (更可靠)
            progress = float(
                celery_info.get("progress")
                if celery_info.get("progress") is not None
                else db_row.progress or 0.0
            )
            message = db_row.message or celery_info.get("msg") or celery_info.get("message", "") or ""

        # 时间字段 (DB 权威)
        started_at = db_row.started_at
        finished_at = db_row.finished_at if state in _CELERY_TERMINAL else None

        # epoch
        current_epoch = celery_info.get("epoch")
        if current_epoch is None and isinstance(db_row.history, list) and db_row.history:
            try:
                current_epoch = db_row.history[-1].get("epoch")
            except Exception:
                current_epoch = None
        total_epochs = celery_info.get("total_epochs") or db_row.epochs

        return JobStateSnapshot(
            task_id=task_id,
            state=state,
            progress=progress,
            message=message,
            current_epoch=current_epoch,
            total_epochs=total_epochs,
            started_at=started_at,
            finished_at=finished_at,
            error=db_row.error,
            history=db_row.history if include_history else None,
            source="db" if state in _CELERY_TERMINAL else "merged",
        )

    @staticmethod
    async def get_snapshot_with_fresh_db(
        task_id: str,
        *,
        include_history: bool = False,
    ) -> JobStateSnapshot:
        """开新 session 查 DB (用于 SSE/长轮询, 避免 caller session 锁)

        v2.5.15 P0-4 修复: SSE 流不应持有 caller 提供的 session, 否则
        阻塞其他请求. 这里新建一个 session, 短查短释放.
        """
        async with AsyncSessionLocal() as db:
            return await JobStateService.get_snapshot(
                task_id, db, include_history=include_history
            )

    # ============== 状态机转移 (统一入口) ==============

    @staticmethod
    async def transition_to_state(
        db: AsyncSession,
        job: TrainingJob,
        new_state: str,
        *,
        message: Optional[str] = None,
        progress: Optional[float] = None,
        commit: bool = True,
    ) -> TrainingJob:
        """统一状态机转移入口

        业务规则:
        - 业务方法 (mark_started/mark_succeeded/mark_failed) 内部已含 transfer_to
        - 外部直接修改 state 字段是反模式, 必须走本方法
        - 非法转移抛 ValueError, 由 caller 决定是否回滚

        Args:
            db: AsyncSession (caller 提供)
            job: 已加载的 TrainingJob
            new_state: 目标状态
            message: 可选, 更新 message 字段
            progress: 可选, 更新 progress 字段 (0-100)
            commit: 是否立即 commit (默认 True; 批量更新时设 False 由 caller 统一 commit)
        """
        if new_state == TRAIN_STATE_PROGRESS:
            job.mark_started()
        elif new_state == TRAIN_STATE_SUCCESS:
            job.mark_succeeded()
        elif new_state == TRAIN_STATE_FAILURE:
            # mark_failed 需要 error, 这里走 transition_to
            job.transition_to(TRAIN_STATE_FAILURE, message=message or job.message)
            from datetime import datetime
            job.finished_at = datetime.utcnow()
            if job.started_at:
                job.duration_seconds = (job.finished_at - job.started_at).total_seconds()
        elif new_state == TRAIN_STATE_REVOKED:
            job.transition_to(TRAIN_STATE_REVOKED, message=message or job.message)
        else:
            # PENDING / PAUSED 等
            job.transition_to(new_state, message=message)

        if progress is not None:
            job.update_progress(progress, message=None)

        if commit:
            await db.commit()
            await db.refresh(job)
        return job

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        job: TrainingJob,
        error: str,
        *,
        commit: bool = True,
    ) -> TrainingJob:
        """统一失败入口 (从 worker / API 共用)

        自动处理: state=FAILURE / error 截断 / finished_at / duration
        """
        job.mark_failed(error)
        if commit:
            await db.commit()
            await db.refresh(job)
        return job

    # ============== 批量 / 工具方法 ==============

    @staticmethod
    async def list_active_snapshots(db: AsyncSession) -> List[JobStateSnapshot]:
        """列出所有进行中任务快照 (前端仪表盘用)"""
        from app.model.training_queries import list_active_jobs
        jobs = await list_active_jobs(db)
        snapshots = []
        for job in jobs:
            if not job.celery_task_id:
                continue
            snap = await JobStateService.get_snapshot(job.celery_task_id, db)
            snapshots.append(snap)
        return snapshots

    @staticmethod
    def celery_state_from_db(db_state: str) -> str:
        """DB 状态 → Celery 状态 (统一映射, 避免散落字面量)"""
        # TrainingJob.state 与 Celery 状态值相同, 此函数保留扩展点
        return db_state

    @staticmethod
    async def revoke(
        db: AsyncSession,
        job: TrainingJob,
        *,
        terminate: bool = False,
        commit: bool = True,
    ) -> TrainingJob:
        """撤销任务 (从 API 入口, 业务编排由本服务统一)

        Args:
            terminate: True 强制 kill worker (SIGTERM), False 仅取消
        """
        # 1) DB 状态转移
        if not job.is_terminal():
            job.transition_to(TRAIN_STATE_REVOKED, message="用户撤销")
            from datetime import datetime
            job.finished_at = datetime.utcnow()

        # 2) Celery 端 revoke
        if job.celery_task_id:
            try:
                _ca = _get_celery_app_safe()
                if _ca is not None:
                    _ca.control.revoke(job.celery_task_id, terminate=terminate)
            except Exception:
                # Celery 不可达 → DB 状态已切, 前端轮询即可
                pass

        if commit:
            await db.commit()
            await db.refresh(job)
        return job


__all__ = [
    "JobStateService",
    "JobStateSnapshot",
]
