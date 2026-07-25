"""
training_lifecycle_service.job — TrainingJob 创建/重置/进度/历史/数据统计
=======================================================================

**v3.0.0 Phase S5 拆分**: 从 training_lifecycle_service.py (580行) 抽离
**职责** (按 Phase 5 设计):
- create_or_reset_job / _sync — 创建或重置 TrainingJob 行 (PROGRESS 状态)
- update_job_progress / _sync — 更新 TrainingJob 进度字段 (DB)
- push_history — 训练历史曲线写入 Redis + DB (双写)
- persist_dataset_stats / _sync — 数据集统计写库 (data_total/num_classes 等)

**调用方**: classification.py / detection/train.py / segmentation/train.py 三个 worker
**设计要点**:
- 4 组方法都是 static, 内部用 _run_async 切到事件循环
- 写库失败不阻塞训练主流程 (仅 logger.warning)
- push_history 兼容旧 worker 调用 (仅 task_id + history_buffer)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.utils.async_helpers import run_async_in_worker as _run_async
from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)


# ============== 1. TrainingJob 创建/重置 ==============

async def create_or_reset_job(
    *,
    task_id: str,
    user_id: int,
    dataset_id: int,
    base_model: str,
    model_name: str,
    task_type: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    started_at: datetime,
) -> int:
    """创建或重置 TrainingJob 行 (PROGRESS 状态)

    处理两种场景:
    1) API 预创建 (TrainingService.start_training 已写入 PENDING 行):
       - 找到 existing → state=PROGRESS, 清空 error/finished
       - 保留 "等待 worker 启动..." 消息 (API 预创建标记)
    2) Worker 主动创建 (罕见, 兜底):
       - INSERT 新行
    3) Worker 重投递 (崩溃恢复):
       - existing.message != 预创建文案 → 标 "Re-running"

    Returns:
        TrainingJob.id
    """
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalar_one_or_none()

        if existing is not None:
            # ---- 区分"API 预创建"和"worker 重投递" ----
            is_api_precreated = existing.message in (
                "等待 worker 启动...",
                "任务已入队, 等待 worker 启动...",
            )
            existing.state = "PROGRESS"
            existing.progress = 0.0
            existing.error = None
            if not is_api_precreated:
                existing.message = "Re-running (worker restart recovery)"
            existing.started_at = started_at
            existing.finished_at = None
            existing.duration_seconds = None
            existing.task_type = task_type
            # v2.5.28: 重置数据集统计 (避免上一轮残留)
            existing.data_total = None
            existing.data_train = None
            existing.data_val = None
            existing.num_classes = None
            existing.class_names = None
            await db.commit()
            await db.refresh(existing)
            return existing.id

        # 不存在则 INSERT (兜底, 正常流程下 TrainingService 已预创建)
        job = TrainingJob(
            celery_task_id=task_id,
            user_id=user_id,
            dataset_id=dataset_id,
            base_model=base_model,
            model_name=model_name,
            task_type=task_type,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            state="PROGRESS",
            progress=0.0,
            started_at=started_at,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


def create_or_reset_job_sync(**kwargs) -> int:
    """同步包装: worker 进程直接调, 内部切到事件循环"""
    return _run_async(create_or_reset_job(**kwargs))


# ============== 2. 进度更新 (DB) ==============

async def update_job_progress(
    job_id: int,
    *,
    progress: float,
    message: Optional[str] = None,
    current_epoch: Optional[int] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """更新 TrainingJob 进度字段 (DB)

    与 push_history 配合: 单纯更新 progress/message/epoch,
    history 由 push_history 单独写库避免重复 IO.
    """
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob

    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(TrainingJob, job_id)
            if not job:
                return
            job.progress = float(progress)
            if message is not None:
                job.message = message
            if current_epoch is not None:
                job.current_epoch = current_epoch
            if history is not None:
                job.history = list(history)
            await db.commit()
    except Exception as e:
        # 写库失败不阻塞训练主流程
        logger.warning("update_job_progress failed for job %s: %s", job_id, e)


def update_job_progress_sync(**kwargs) -> None:
    """同步包装"""
    _run_async(update_job_progress(**kwargs))


# ============== 3. 训练历史推送 (Redis + DB) ==============

def push_history(
    task_id: str,
    history_buffer: List[Dict[str, Any]],
    *,
    job_id: Optional[int] = None,
    progress: Optional[float] = None,
    message: Optional[str] = None,
    current_epoch: Optional[int] = None,
) -> None:
    """训练历史曲线写入 Redis + DB

    双写 (Redis 优先, DB 兜底):
    1) Redis train:history:{task_id} — 供前端 /training/history 端点 5s 轮询
    2) TrainingJob.history (DB) — Redis 失效兜底 + 训练后历史保留

    兼容旧 worker 调用方式 (仅 task_id + history_buffer).
    """
    # ---- 1) Redis ----
    if history_buffer:
        try:
            key = f"train:history:{task_id}"
            redis_client.setex(key, 86400, json.dumps(history_buffer))
        except Exception as e:
            logger.warning("push_history redis failed for %s: %s", task_id, e)

    # ---- 2) DB (job_id 存在时) ----
    if job_id is not None:
        update_job_progress_sync(
            job_id=job_id,
            progress=progress or 0.0,
            message=message,
            current_epoch=current_epoch,
            history=history_buffer,
        )


# ============== 4. sticky_meta 持久化 (数据集统计) ==============

async def persist_dataset_stats(task_id: str, extra: Dict[str, Any]) -> None:
    """把数据集统计 (data_total/data_train/data_val/num_classes/class_names) 写库

    触发条件: extra 含 data_total + num_classes 关键字 (避免每个 callback 都写)
    失败不抛: 写库失败不能让训练炸, 仅记日志
    """
    try:
        data_total = extra.get("data_total")
        data_train = extra.get("data_train")
        data_val = extra.get("data_val")
        num_classes = extra.get("num_classes")
        class_names = extra.get("class_names")
        if data_total is None or num_classes is None:
            logger.info(
                "[stats] skip: data_total/num_classes missing for %s", task_id
            )
            return

        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.tasks.model.training_job import TrainingJob

        async with AsyncSessionLocal() as db:
            job = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if not job:
                logger.info("[stats] warn: no TrainingJob row for %s", task_id)
                return
            job.data_total = int(data_total) if data_total is not None else None
            job.data_train = int(data_train) if data_train is not None else None
            job.data_val = int(data_val) if data_val is not None else None
            job.num_classes = int(num_classes) if num_classes is not None else None
            job.class_names = list(class_names) if class_names is not None else None
            await db.commit()
            logger.info(
                "[stats] OK: task=%s db_id=%s data_total=%s num_classes=%s",
                task_id, job.id, data_total, num_classes,
            )
    except Exception as e:
        logger.warning(
            "persist_dataset_stats failed for %s: %s: %s",
            task_id, type(e).__name__, e,
        )


def persist_dataset_stats_sync(task_id: str, extra: Dict[str, Any]) -> None:
    """同步包装"""
    _run_async(persist_dataset_stats(task_id, extra))


__all__ = [
    "create_or_reset_job",
    "create_or_reset_job_sync",
    "update_job_progress",
    "update_job_progress_sync",
    "push_history",
    "persist_dataset_stats",
    "persist_dataset_stats_sync",
]
