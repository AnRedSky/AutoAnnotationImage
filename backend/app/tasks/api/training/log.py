"""
training.log 模块 — 训练日志持久化接口
=======================================

**v3.0.0 Phase O 拆分**: 从 training.py 抽离
**职责**: 训练日志的读取/追加 (SSE 详情页用)

**路由清单** (2 个):
- GET  /jobs/{job_id}/log    读取已持久化的日志
- POST /jobs/{job_id}/log    追加一行 (SSE 收到推送时调用)

**设计原则**:
- 后端仅追加/读取, 不解析
- 最多保留 TrainingJob.LOG_MAX_LINES (200) 行, 超出截断头部
- 空行/超长行 (> 2KB) 直接拒绝 (避免脏数据)

**v3.3.0 P0 修复**: 鉴权 + 所有权校验
- 之前: 任何登录用户可读写任意 job 的日志
- 现在: 必须校验 job.user_id == current_user.id 或 team 共享

**v3.3.5-PERMISSION-REWRITE**: 严格最小权限
- super_admin 不再自动放行, 必须满足 owner / team 成员
- 统一通过 assert_can_access_training_job 校验
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.model.training_job import TrainingJob
from app.tasks.service.permission_service import (
    assert_can_access_training_job,
    log_permission_denied,
)
from app.schemas.training import TrainingJobLogAppend, TrainingJobLogOut

router = APIRouter()

LOG_LINE_MAX_LEN = 2048  # 单行最大 2KB


async def _assert_can_access_job(
    job: TrainingJob,
    current_user: User,
    db: AsyncSession,
    *,
    endpoint: str = "",
) -> None:
    """v3.3.0 P0: 校验用户对训练任务的访问权 (v3.3.5 严格最小权限).

    v3.3.5 核心变化:
      - 任何角色 (含 super_admin) 必须满足 owner / dataset.team_member
      - 移除 v3.3.4-PATCH 的 super_admin 旁路
      - 失败写 permission_denied 审计
    """
    try:
        await assert_can_access_training_job(
            db, current_user, job.user_id, job.dataset_id,
        )
    except HTTPException:
        await log_permission_denied(
            db,
            current_user=current_user,
            resource_type="training_job",
            resource_id=job.id,
            endpoint=endpoint or f"/api/training/jobs/{job.id}/log",
            reason="无权访问此训练任务日志",
            detail={"job_user_id": job.user_id, "job_dataset_id": job.dataset_id},
        )
        raise


@router.get("/jobs/{job_id}/log", response_model=TrainingJobLogOut)
async def get_training_log(
    job_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    读取训练任务已持久化的日志 (SSE 推送过的每行, 详情页打开时拉取)
    持久化由前端 /api/training/jobs/{id}/log POST 触发, 后端只存不解析

    v3.3.0 P0 修复: 必须校验所有权, 防止跨用户日志泄露
    v3.3.5 重写: 严格最小权限, super_admin 不再旁路
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    await _assert_can_access_job(
        job, current_user, db,
        endpoint=f"/api/training/jobs/{job_id}/log",
    )
    log = job.log if isinstance(job.log, list) else []
    return TrainingJobLogOut(log=log)


@router.post("/jobs/{job_id}/log", response_model=TrainingJobLogOut)
async def append_training_log(
    job_id: int,
    payload: TrainingJobLogAppend,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    追加一行训练日志 (前端 SSE 收到推送时调用)
    - 后端仅追加到 TrainingJob.log 字段 (JSON list)
    - 最多保留 LOG_MAX_LINES (200) 行, 超出时截断头部
    - 空行/超长行 (超过 2KB) 直接拒绝, 避免脏数据

    v3.3.0 P0 修复: 必须校验所有权, 防止跨用户日志篡改
    v3.3.5 重写: 严格最小权限, super_admin 不再旁路
    """
    line = (payload.line or "").rstrip()
    if not line:
        return TrainingJobLogOut(log=[])
    if len(line) > LOG_LINE_MAX_LEN:
        return TrainingJobLogOut(log=[])  # 静默丢弃

    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    await _assert_can_access_job(
        job, current_user, db,
        endpoint=f"/api/training/jobs/{job_id}/log",
    )
    log = list(job.log) if isinstance(job.log, list) else []
    log.append(line)
    # 截断头部, 保留尾部 LOG_MAX_LINES 行
    if len(log) > TrainingJob.LOG_MAX_LINES:
        log = log[-TrainingJob.LOG_MAX_LINES:]
    job.log = log
    await db.commit()
    return TrainingJobLogOut(log=log)
