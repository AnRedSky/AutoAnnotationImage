"""
training.jobs 模块 — 训练任务 CRUD + 状态控制 + 错误查询
==========================================================

**v3.0.0 Phase O 拆分**: 从 training.py 抽离
**职责**: 任务查询/详情/取消/暂停/编辑/删除/错误查询

**路由清单** (7 个):
- GET    /jobs                      训练任务列表 (分页 + 过滤)
- GET    /jobs/{job_id}             单个任务详情
- POST   /jobs/{job_id}/cancel      取消 (Celery revoke + DB REVOKED)
- POST   /jobs/{job_id}/error       读取错误详情 (DB + Redis 双源)
- POST   /jobs/{job_id}/pause       暂停 (Redis 标志 + revoke + DB PAUSED)
- PATCH  /jobs/{job_id}             更新参数 (PENDING/PAUSED/终态允许, PROGRESS 禁止)
- DELETE /jobs/{job_id}             删除记录 (终态允许, 清 Redis 残留)
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.redis import redis_client
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.model.training_job import TrainingJob
from app.schemas.training import (
    TrainingJobList,
    TrainingJobOut,
    TrainingJobActionResult,
    TrainingJobUpdate,
)

router = APIRouter()


# ============== 任务查询 ==============

@router.get("/jobs", response_model=TrainingJobList)
@router.get("/jobs/", response_model=TrainingJobList)  # 兼容前端带尾斜杠的写法
async def list_training_jobs(
    dataset_id: Optional[int] = None,
    state: Optional[str] = Query(default=None, description="可选按状态过滤, e.g. PROGRESS/SUCCESS/FAILURE/PAUSED/REVOKED"),
    task_type: Optional[str] = Query(
        default=None,
        description=(
            "可选按任务类型过滤, e.g. classification/detection/segmentation. "
            "v2.5.24: 支持逗号分隔的多值, e.g. 'classification,detection'."
        ),
    ),
    q: Optional[str] = Query(
        default=None,
        description=(
            "关键词模糊搜索: 同时匹配 model_name 与 base_model 两个字段 (LIKE 不区分大小写). "
            "如 'resnet' 会匹配 model_name='resnet50_v1_xxx' 与 base_model='resnet50' 两种结果."
        ),
    ),
    page: int = Query(default=1, ge=1, description="1-based 页码"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页条数 (1-100)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    训练任务历史列表 (训练运行历史数据)
    - 按 dataset_id / state 过滤 (state 可选)
    - 按 q 关键词在 model_name / base_model 两个字段做模糊匹配 (大小写不敏感)
    - 分页: page (>=1) + page_size (1-100, 默认 10)
    - 返回 total + items, 前端 el-pagination 直接用
    """
    base = select(TrainingJob)
    count_base = select(sa_func.count(TrainingJob.id))
    if dataset_id is not None:
        base = base.where(TrainingJob.dataset_id == dataset_id)
        count_base = count_base.where(TrainingJob.dataset_id == dataset_id)
    if state:
        base = base.where(TrainingJob.state == state)
        count_base = count_base.where(TrainingJob.state == state)
    if task_type and task_type.strip():
        # v2.5.24: 支持逗号分隔的多值 (前端可能同时选 classification + detection)
        types = [t.strip() for t in task_type.split(",") if t.strip()]
        if len(types) == 1:
            base = base.where(TrainingJob.task_type == types[0])
            count_base = count_base.where(TrainingJob.task_type == types[0])
        elif len(types) > 1:
            base = base.where(TrainingJob.task_type.in_(types))
            count_base = count_base.where(TrainingJob.task_type.in_(types))
    if q and q.strip():
        like_pat = f"%{q.strip()}%"
        # 关键词同时作用于 model_name 与 base_model, 用 OR 连接
        kw_filter = or_(
            TrainingJob.model_name.ilike(like_pat),
            TrainingJob.base_model.ilike(like_pat),
        )
        base = base.where(kw_filter)
        count_base = count_base.where(kw_filter)

    total = (await db.execute(count_base)).scalar_one()
    offset = (page - 1) * page_size
    stmt = base.order_by(TrainingJob.id.desc()).offset(offset).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return TrainingJobList(total=total, items=rows, page=page, page_size=page_size)


@router.get("/jobs/{job_id}", response_model=TrainingJobOut)
async def get_training_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个训练任务详情"""
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    return job


# ============== 任务控制 ==============

@router.post("/jobs/{job_id}/cancel")
async def cancel_training_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    取消/停止一个训练任务
    - 仅当任务处于 PENDING/PROGRESS 时生效
    - 同步发送 Celery revoke 信号
    - 更新 TrainingJob 状态为 REVOKED
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state not in ("PENDING", "PROGRESS"):
        return {
            "success": False,
            "message": f"Job is already in terminal state: {job.state}",
            "state": job.state,
        }

    # 1) 发 Celery revoke
    if job.celery_task_id:
        try:
            from app.tasks.workers.celery_app import celery_app
            celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:
            # broker 不可用时不强失败, 只更新 DB
            pass

    # 2) 更新 DB
    job.state = "REVOKED"
    job.message = "Cancelled by user"
    job.finished_at = datetime.utcnow()
    if job.started_at:
        job.duration_seconds = (job.finished_at - job.started_at).total_seconds()
    await db.commit()

    return {"success": True, "state": "REVOKED", "job_id": job_id}


@router.post("/jobs/{job_id}/error")
async def get_training_error(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    读取训练任务的错误详情
    错误信息既存在 TrainingJob.error 字段, 也存在 Redis train:error:{task_id} 兜底
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    err = job.error
    redis_err = None
    if job.celery_task_id:
        try:
            raw = redis_client.get(f"train:error:{job.celery_task_id}")
            if raw:
                try:
                    redis_err = json.loads(raw)
                except (ValueError, TypeError):
                    redis_err = {"raw": raw}
        except Exception:
            pass
    return {
        "job_id": job_id,
        "state": job.state,
        "db_error": err,
        "redis_error": redis_err,
    }


@router.post("/jobs/{job_id}/pause", response_model=TrainingJobActionResult)
async def pause_training_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """暂停一个训练任务

    - 仅当任务处于 PENDING/PROGRESS 时生效
    - 写 Redis train:pause:{task_id} 标志位, worker 在每个 epoch 起点检测
    - 同步 revoke Celery task (terminate=False, 不强杀 SIGTERM; 让 worker 走优雅退出)
    - DB 状态先标 PAUSED (UI 立即可见); worker 实际停下后会写完整消息
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state not in ("PENDING", "PROGRESS"):
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Cannot pause a job in state {job.state}",
        )

    # 1) Redis 标志位 (worker 下个 epoch 起点读到即抛 TrainingPaused)
    if job.celery_task_id:
        try:
            redis_client.setex(f"train:pause:{job.celery_task_id}", 3600, "1")
        except Exception:
            pass  # Redis 不可用不强失败, DB 状态已写

    # 2) revoke Celery (terminate=False, 不强杀, 等 worker 走 epoch 边界正常退出)
    if job.celery_task_id:
        try:
            from app.tasks.workers.celery_app import celery_app
            celery_app.control.revoke(job.celery_task_id, terminate=False)
        except Exception:
            pass

    # 3) DB 状态 (UI 立即可见 PAUSED, 不等 worker 跑完这个 epoch)
    job.state = "PAUSED"
    job.message = "Pause requested by user"
    await db.commit()

    return TrainingJobActionResult(
        success=True, job_id=job_id, state="PAUSED",
        message="Pause signal sent. Worker will stop at next epoch boundary.",
    )


# ============== 任务编辑/删除 ==============

@router.patch("/jobs/{job_id}", response_model=TrainingJobOut)
async def update_training_job(
    job_id: int,
    payload: TrainingJobUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新训练任务参数 (任务未运行才允许)

    - 允许状态: PENDING (未启动) / PAUSED (已暂停) / SUCCESS / FAILURE / REVOKED (终态, 可调整参数后重跑)
    - 禁止状态: PROGRESS (运行中, 参数已生效, 改了下次才生效但易引起混淆)
    - 仅更新提供的字段 (PUT 语义的部分更新)
    - 同步重置 message (清掉旧的 "等待 worker" 文案, 让用户知道参数刚改过)
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state == "PROGRESS":
        raise HTTPException(409, f"任务 #${job_id} 正在训练中, 暂不允许编辑参数")

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        # 没有任何字段, 啥都不做, 直接返回
        return job

    # 业务校验: model_name 唯一性 (同 dataset 下不与历史冲突)
    if "model_name" in update_data or "dataset_id" in update_data:
        target_dataset = update_data.get("dataset_id", job.dataset_id)
        target_name = update_data.get("model_name", job.model_name)
        dup = (await db.execute(
            select(TrainingJob).where(
                TrainingJob.id != job_id,
                TrainingJob.dataset_id == target_dataset,
                TrainingJob.model_name == target_name,
            )
        )).scalars().first()
        if dup:
            raise HTTPException(400, f"模型版本名 {target_name!r} 在数据集中已存在 (job #{dup.id}), 请换一个")

    for k, v in update_data.items():
        setattr(job, k, v)
    job.message = f"参数已更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, 可点击启动"
    await db.commit()
    await db.refresh(job)
    return job


@router.delete("/jobs/{job_id}", response_model=TrainingJobActionResult)
async def delete_training_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除训练任务记录

    - 仅当任务处于终态 (SUCCESS/FAILURE/REVOKED/PAUSED) 时允许删除
    - 物理删除 DB 记录, 不可恢复
    - 同步清 Redis 中的 history/error/pause 缓存 (避免历史数据残留)
    - 不影响已生成的 ModelVersion 记录 (用户可单独管理)
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state in ("PENDING", "PROGRESS"):
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Cannot delete a running job in state {job.state}. Cancel it first.",
        )

    # 清 Redis 残留
    if job.celery_task_id:
        for key in (
            f"train:history:{job.celery_task_id}",
            f"train:error:{job.celery_task_id}",
            f"train:pause:{job.celery_task_id}",
        ):
            try:
                redis_client.delete(key)
            except Exception:
                pass

    await db.delete(job)
    await db.commit()

    return TrainingJobActionResult(
        success=True, job_id=job_id, state=job.state,
        message=f"Training job #{job_id} deleted",
    )
