"""
training.start 模块 — 训练任务启动 (新建 + 复用旧任务)
=====================================================

**v3.0.0 Phase O 拆分**: 从 training.py 抽离
**职责**:
- start_training: 全新训练 (POST /api/training/start)
- start_existing_training_job: 复用旧任务 (POST /api/training/jobs/{id}/start, mode=restart|resume)

**共享工具**:
- _build_task_kwargs: 按 task_type 构建 Celery task kwargs
- _apply_training_task: 按 task_type 投递到对应 Celery 任务
- _create_pending_restart_job: 预创建 PENDING 行 (避免 worker 启动前 GET /jobs 查不到)
- _rollback_restart: 预创建失败回滚

**Phase 3 业务下沉**: start_training 已 thin wrapper, 业务逻辑 (broker 校验/失败回滚)
全部在 TrainingService.start_training.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.model.training_job import TrainingJob
from app.tasks.model.model_version import ModelVersion
from app.tasks.workers.classification import train_model_task
from app.schemas.training import (
    TrainStartResponse,
    TrainingJobActionResult,
    TrainingJobUpdate,
)
from app.tasks.service.training_service import TrainingService

router = APIRouter()


# ============== 共享工具 ==============

def _build_task_kwargs(
    task_type: str,
    *,
    dataset_id: int,
    user_id: int,
    base_model: str,
    model_name: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    pretrained_model_path: Optional[str] = None,
) -> dict:
    """按 task_type 构建对应 Celery 任务的 kwargs.

    - classification: timm 微调 (base_model=..., model_name=别名, 支持增量权重)
    - detection:     ultralytics YOLO (model_name=权重名 yolov8n/..., model_alias=落盘名)
    - segmentation:   torchvision DeepLabV3+ (backbone=..., model_alias=落盘名)

    detection/segmentation 的 task 签名不接受 pretrained_model_path
    (YOLO 用 ultralytics 自带预训练权重, DeepLab 用 torchvision 预训练), 故忽略.
    """
    if task_type == "detection":
        return dict(
            dataset_id=dataset_id,
            user_id=user_id,
            model_name=base_model,    # yolov8n/s/m/l/x
            model_alias=model_name,  # 落盘 ModelVersion.name
            epochs=epochs,
            batch=batch_size,
        )
    if task_type == "segmentation":
        return dict(
            dataset_id=dataset_id,
            user_id=user_id,
            backbone=base_model,     # deeplabv3_resnet50/101
            model_alias=model_name,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
        )
    # classification (默认)
    return dict(
        dataset_id=dataset_id,
        base_model=base_model,
        model_name=model_name,
        user_id=user_id,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        pretrained_model_path=pretrained_model_path if pretrained_model_path else None,
    )


def _apply_training_task(task_type: str, kwargs: dict, *, task_id: Optional[str] = None):
    """按 task_type 选择 Celery 任务并投递.

    - task_id 非空: apply_async(task_id=...) 强制使用预生成 ID (预创建行场景)
    - task_id 为空: delay() 让 Celery 自动生成 ID (resume 复用旧 job 场景,
      随后由调用方把 job.celery_task_id 更新为 task.id)
    """
    if task_type == "detection":
        from app.tasks.workers.detection import train_detection_task
        task = train_detection_task
    elif task_type == "segmentation":
        from app.tasks.workers.segmentation import train_segmentation_task
        task = train_segmentation_task
    else:
        task = train_model_task
    if task_id:
        return task.apply_async(kwargs=kwargs, task_id=task_id)
    return task.delay(**kwargs)


async def _create_pending_restart_job(
    *,
    celery_task_id: str,
    user_id: int,
    dataset_id: int,
    base_model: str,
    model_name: str,
    task_type: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
) -> int | None:
    """预创建 PENDING 行 (mode=restart)
    - 与 start_training 不同: 显式传 started_at=None, 避免 SQLAlchemy 自动填
      default=datetime.utcnow (这会让 PENDING 阶段就显示"已开始")
    - 容错: 如果 inserted_primary_key 拿不到 (极端情况), 返回 None
    - 重试: aiomysql 偶发 MySQLServerHasGoneAway 时, 一次重试, 仍失败则抛

    注意: 必须写成 async def, 直接 await _do().
    早期写成 def + _run_async(_do()) 在 async 上下文里会触发
    "Cannot run the event loop while another loop is running"
    """
    async def _do():
        async with AsyncSessionLocal() as sdb:
            result = await sdb.execute(
                mysql_insert(TrainingJob).values(
                    celery_task_id=celery_task_id,
                    user_id=user_id,
                    dataset_id=dataset_id,
                    base_model=base_model,
                    model_name=model_name,
                    task_type=task_type,
                    epochs=epochs,
                    batch_size=batch_size,
                    learning_rate=learning_rate,
                    state="PENDING",
                    progress=0.0,
                    message="等待 worker 启动...",
                    created_at=datetime.utcnow(),
                    started_at=None,
                    finished_at=None,
                )
            )
            await sdb.commit()
            pk = result.inserted_primary_key
            return pk[0] if pk else None
    try:
        return await _do()
    except Exception:
        # 重试一次 (偶发 MySQLServerHasGoneAway / 连接池抖动)
        return await _do()


async def _rollback_restart(new_job_id: int) -> None:
    """删除预创建的行 (apply_async 失败时调用)"""
    async with AsyncSessionLocal() as sdb:
        await sdb.execute(
            delete(TrainingJob).where(TrainingJob.id == new_job_id)
        )
        await sdb.commit()


# ============== 路由 ==============

@router.post("/start", response_model=TrainStartResponse)
async def start_training(
    dataset_id: int,
    base_model: str = "efficientnet_b0",
    model_name: str = "",
    epochs: int = 20,
    batch_size: int = 32,
    learning_rate: float = 1e-4,
    pretrained_model_path: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    启动异步训练任务 (v3.0.0 Phase 3: thin wrapper, 业务下沉到 TrainingService)

    - 提交到 Celery worker
    - 返回 task_id 供前端轮询
    - model_name 兜底 / broker 校验 / 预创建行 / 失败回滚 全部在 TrainingService

    pretrained_model_path: 增量训练 (再训练) 时, 传入 .pth 文件路径作为模型起点
    - 空字符串 (默认): 从头微调 (timm ImageNet 预训练权重)
    - 已有路径: 加载该 .pth 的 state_dict (fine-tune 旧模型)
    """
    # P0-5: 非 admin 只能对自己的 dataset 启训练
    from app.tasks.model.dataset import Dataset
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset, require_write=True)

    result = await TrainingService.start_training(
        db,
        user_id=current_user.id,
        dataset_id=dataset_id,
        base_model=base_model,
        model_name=model_name,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        pretrained_model_path=pretrained_model_path,
    )

    # MT-8: 审计日志
    from app.tasks.service.audit_service import log_audit
    await log_audit(db, user_id=current_user.id, event_type="training_started",
                    resource_type="training_job", resource_id=result.get("job_id"),
                    detail={"dataset_id": dataset_id, "base_model": base_model,
                            "epochs": epochs, "model_name": model_name})
    await db.commit()

    return TrainStartResponse(**result)


@router.post("/jobs/{job_id}/start", response_model=TrainingJobActionResult)
async def start_existing_training_job(
    job_id: int,
    mode: str = Query(default="restart", description="restart=再训练 (新 job_id) | resume=继续 (复用同 job, 不改 model_name)"),
    payload: Optional[TrainingJobUpdate] = Body(default=None, description="参数覆盖 (仅 mode=restart 生效, mode=resume 必须用原 job 参数)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """复用旧任务配置重新提交 Celery 训练任务

    - mode=restart (默认, 用于终态任务的「再训练」按钮):
        * 用旧 job 的 (dataset_id, base_model, model_name, epochs, batch_size, learning_rate)
        * 可选 payload 参数覆盖 (再训练弹窗): 用于「原任务不动, 仅作为新任务的训练参数」场景
        * 新 model_name = 原 model_name + _r{timestamp} 后缀 (避免 .pth 文件冲突)
        * 旧 job 记录保持不变, worker 接手时写新 PENDING 记录
    - mode=resume (用于 PAUSED 任务的「继续」按钮):
        * 复用原 job 的全部配置, 包括 model_name (覆盖现有 .pth 即可)
        * 旧 job 记录继续使用, worker 重新执行训练
        * 不允许传 payload (语义: 继续 = 断点续训, 不能换参数)
    - 若旧 job 仍在 PENDING/PROGRESS, 返回错误
    """
    if mode not in ("restart", "resume"):
        raise HTTPException(400, f"invalid mode: {mode}, must be 'restart' or 'resume'")

    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state in ("PENDING", "PROGRESS"):
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Job is still running in state {job.state}. Cancel it first.",
        )

    # resume 模式要求任务处于 PAUSED 状态 (其他终态走 restart)
    if mode == "resume" and job.state != "PAUSED":
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Only PAUSED job can be resumed; current state is {job.state}",
        )

    # ---- 计算最终训练参数 (旧 job 字段为基底, payload 覆盖) ----
    overrides = payload.model_dump(exclude_unset=True) if payload else {}

    if mode == "resume":
        if overrides:
            raise HTTPException(
                400,
                "resume 模式不允许修改参数 (继续训练 = 断点续训). 如需新参数, 请用「再训练」"
            )
        final_dataset_id = job.dataset_id
        final_base_model = job.base_model
        final_epochs = job.epochs
        final_batch_size = job.batch_size
        final_learning_rate = job.learning_rate
        new_model_name = job.model_name
        final_task_type = (job.task_type or "classification").lower()
    else:
        # restart: 应用 payload 覆盖 (不修改 job 记录)
        final_dataset_id = overrides.get("dataset_id", job.dataset_id)
        final_base_model = overrides.get("base_model", job.base_model)
        final_epochs = overrides.get("epochs", job.epochs)
        final_batch_size = overrides.get("batch_size", job.batch_size)
        final_learning_rate = overrides.get("learning_rate", job.learning_rate)
        final_task_type = (job.task_type or "classification").lower()
        # model_name: 强制加后缀 (即使 payload 改了 model_name, 也再加一层时间戳)
        # v3.0.0 修复: 先剥离已有的 _r{digits} 后缀, 避免多次再训练后名称无限增长
        # (之前: resnet50_v1_r123_r456_r789... → 超过列长度 64 报 DataError)
        import re
        suffix = f"_r{int(datetime.utcnow().timestamp())}"
        payload_model = overrides.get("model_name", job.model_name)
        base_name = re.sub(r'_r\d+$', '', payload_model)  # 剥离末尾的 _r{timestamp}
        new_model_name = f"{base_name}{suffix}"

    # ---- 增量训练 (再训练) 核心: 自动用当前激活的模型权重 ----
    pretrained_model_path = None
    pretrained_source_mv_id = None
    if mode == "restart":
        active_mv = (await db.execute(
            select(ModelVersion)
            .where(
                ModelVersion.dataset_id == final_dataset_id,
                ModelVersion.is_active == True,  # noqa: E712
            )
            .order_by(ModelVersion.id.desc())
        )).scalars().first()
        if active_mv and active_mv.file_path:
            pth = Path(active_mv.file_path)
            if pth.exists():
                pretrained_model_path = str(pth)
                pretrained_source_mv_id = active_mv.id

    # ---- 预创建 TrainingJob 行 (mode=restart) ----
    new_job_id: int | None = None
    celery_task_id_to_use: str | None = None

    if mode == "restart":
        celery_task_id_to_use = uuid4().hex
        new_job_id = await _create_pending_restart_job(
            celery_task_id=celery_task_id_to_use,
            user_id=current_user.id,
            dataset_id=final_dataset_id,
            base_model=final_base_model,
            model_name=new_model_name,
            task_type=final_task_type,
            epochs=final_epochs,
            batch_size=final_batch_size,
            learning_rate=final_learning_rate,
        )
        if new_job_id is None:
            # 防御性兜底: 预创建返回 None, 直接 500
            raise HTTPException(500, "预创建训练任务行失败 (inserted_primary_key 为空), 请重试")

    try:
        apply_kwargs = _build_task_kwargs(
            final_task_type,
            dataset_id=final_dataset_id,
            user_id=current_user.id,
            base_model=final_base_model,
            model_name=new_model_name,
            epochs=final_epochs,
            batch_size=final_batch_size,
            learning_rate=final_learning_rate,
            pretrained_model_path=pretrained_model_path,
        )
        if mode == "restart":
            task = _apply_training_task(final_task_type, apply_kwargs, task_id=celery_task_id_to_use)
        else:
            task = _apply_training_task(final_task_type, apply_kwargs)
    except Exception as e:
        # 入队失败, 回滚预创建的行
        if new_job_id is not None:
            try:
                await _rollback_restart(new_job_id)
            except Exception:
                pass
        err_msg = str(e)[:200]
        if any(k in err_msg.lower() for k in ["connection", "refused", "redis", "broker"]):
            raise HTTPException(503, f"Celery broker unavailable: {err_msg}")
        raise HTTPException(500, f"Failed to submit training task: {err_msg}")

    # resume 模式: 立刻把 DB 中的 job 状态从 PAUSED 改回 PENDING
    if mode == "resume":
        job.state = "PENDING"
        job.progress = 0.0
        job.celery_task_id = task.id
        # started_at 保留 None, 由 worker 接手时填充
        # job.started_at 维持原值, 因为语义上是"任务整体首次开始"的时间
        job.finished_at = None
        job.error = None
        await db.commit()
        await db.refresh(job)

    return TrainingJobActionResult(
        success=True,
        job_id=job_id,
        new_job_id=new_job_id,
        state="PENDING",
        message=(
            f"Resumed training (model_name={new_model_name})"
            if mode == "resume"
            else (
                f"New training task #{new_job_id} created (model_name={new_model_name}, "
                f"原任务 #{job_id} 保持不变, "
                f"{'增量训练: 基于 ModelVersion #' + str(pretrained_source_mv_id) if pretrained_source_mv_id else '从头微调 (无激活模型, 使用 ImageNet 预训练)'})"
            )
        ),
        task_id=task.id,
    )
