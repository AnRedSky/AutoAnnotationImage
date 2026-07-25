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
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.model.training_job import TrainingJob
from app.schemas.training import TrainingJobLogAppend, TrainingJobLogOut

router = APIRouter()

LOG_LINE_MAX_LEN = 2048  # 单行最大 2KB


@router.get("/jobs/{job_id}/log", response_model=TrainingJobLogOut)
async def get_training_log(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    读取训练任务已持久化的日志 (SSE 推送过的每行, 详情页打开时拉取)
    持久化由前端 /api/training/jobs/{id}/log POST 触发, 后端只存不解析
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    log = job.log if isinstance(job.log, list) else []
    return TrainingJobLogOut(log=log)


@router.post("/jobs/{job_id}/log", response_model=TrainingJobLogOut)
async def append_training_log(
    job_id: int,
    payload: TrainingJobLogAppend,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    追加一行训练日志 (前端 SSE 收到推送时调用)
    - 后端仅追加到 TrainingJob.log 字段 (JSON list)
    - 最多保留 LOG_MAX_LINES (200) 行, 超出时截断头部
    - 空行/超长行 (超过 2KB) 直接拒绝, 避免脏数据
    """
    line = (payload.line or "").rstrip()
    if not line:
        return TrainingJobLogOut(log=[])
    if len(line) > LOG_LINE_MAX_LEN:
        return TrainingJobLogOut(log=[])  # 静默丢弃

    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    log = list(job.log) if isinstance(job.log, list) else []
    log.append(line)
    # 截断头部, 保留尾部 LOG_MAX_LINES 行
    if len(log) > TrainingJob.LOG_MAX_LINES:
        log = log[-TrainingJob.LOG_MAX_LINES:]
    job.log = log
    await db.commit()
    return TrainingJobLogOut(log=log)
