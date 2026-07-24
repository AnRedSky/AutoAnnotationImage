"""
TrainingService — 训练任务编排服务
=================================

**职责**:
- 启动训练任务 (按 task_type 分发到对应 Celery 任务)
- 预创建 TrainingJob 行 (消除竞态)
- 校验 broker 可用性
- 封装 task_kwargs 构建

**API 接入**:
- 旧: `app/api/training.py:start_training` 1151 行混合 HTTP / 业务逻辑
- 新: API 层只接参 + 调 service, 业务规则全部下沉

**与 JobStateService 的关系**:
- TrainingService 负责"启动" (start / cancel / retry)
- JobStateService 负责"查询" (snapshot / transition)
- 两者通过 TrainingJob ORM 模型共享数据, 无循环依赖

v3.0.0 Phase 3 新增
"""
from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.model.dataset import Dataset
from app.model.training_job import TrainingJob

logger = logging.getLogger(__name__)


# 任务签名分发表 (3 种任务类型 → Celery 函数 + kwargs 构造)
_TASK_DISPATCH: Dict[str, str] = {
    "classification": "app.workers.tasks:train_model_task",
    "detection": "app.workers.detection_tasks:train_detection_task",
    "segmentation": "app.workers.segmentation_tasks:train_segmentation_task",
}


def _check_broker_sync(host: str, port: int) -> None:
    """同步的 broker TCP 探测 (供 asyncio.to_thread 调用)"""
    s = socket.create_connection((host, port), timeout=2.0)
    s.close()


class TrainingService:
    """训练任务编排服务 (无状态, 静态方法)"""

    # ============== 启动 ==============

    @staticmethod
    async def start_training(
        db: AsyncSession,
        *,
        user_id: int,
        dataset_id: int,
        base_model: str,
        model_name: str,
        epochs: int,
        batch_size: int,
        learning_rate: float,
        pretrained_model_path: str = "",
    ) -> Dict[str, Any]:
        """启动训练任务 (统一入口)

        流程:
        1. 校验数据集 + 决定 task_type
        2. 校验 Redis broker 可用
        3. 预生成 celery_task_id, 预创建 TrainingJob 行
        4. 投递 Celery 任务 (按 task_type 分发)
        5. 失败回滚预创建的行

        Returns:
            {"task_id": str, "celery_task_id": str, "job_id": int, "state": "PENDING"}
        """
        # ---- 1) 数据集 + task_type ----
        ds = await db.get(Dataset, dataset_id)
        if not ds:
            raise HTTPException(404, f"Dataset id={dataset_id} not found")
        task_type = (ds.task_type or "classification").lower()

        # model_name 兜底
        if not model_name or not model_name.strip():
            ts = int(datetime.utcnow().timestamp()) % 10000000000
            model_name = f"{base_model}_v1_{ts}"

        # ---- 2) broker 健康检查 ----
        broker_host = settings.REDIS_HOST
        broker_port = settings.REDIS_PORT
        try:
            await asyncio.to_thread(_check_broker_sync, broker_host, broker_port)
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"Celery broker (Redis) at {broker_host}:{broker_port} unavailable: {e}. "
                       f"Please start Redis and the Celery worker.",
            )

        # ---- 3) 预创建 PENDING 行 ----
        celery_task_id = uuid.uuid4().hex
        job_id = await TrainingService._create_pending_job(
            celery_task_id=celery_task_id,
            user_id=user_id,
            dataset_id=dataset_id,
            base_model=base_model,
            model_name=model_name,
            task_type=task_type,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
        )

        # ---- 4) 投递 Celery 任务 ----
        kwargs = TrainingService.build_task_kwargs(
            task_type,
            dataset_id=dataset_id,
            user_id=user_id,
            base_model=base_model,
            model_name=model_name,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            pretrained_model_path=pretrained_model_path,
        )

        try:
            task = await asyncio.to_thread(
                TrainingService._apply_training_task,
                task_type, kwargs, celery_task_id,
            )
        except Exception as e:
            # 入队失败, 回滚预创建的行
            await TrainingService._rollback_pending_job(job_id)
            err_msg = str(e)[:200]
            if any(k in err_msg.lower() for k in ["connection", "refused", "redis", "broker", "timeout"]):
                raise HTTPException(
                    status_code=503,
                    detail=f"Celery broker unavailable: {err_msg}. "
                           f"Please start Redis and the Celery worker.",
                )
            raise HTTPException(500, f"Failed to submit training task: {err_msg}")

        # Celery task_id 必须等于我们预生成的 (apply_async(task_id=...) 强制)
        assert task.id == celery_task_id, (
            f"Celery task id mismatch: {task.id} != {celery_task_id}"
        )

        return {
            "task_id": task.id,
            "celery_task_id": task.id,
            "job_id": job_id,
            "state": "PENDING",
            "message": "Training task submitted",
        }

    # ============== 内部: DB 预创建 ==============

    @staticmethod
    async def _create_pending_job(
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
    ) -> int:
        """预创建 TrainingJob 行 (state=PENDING)

        必须在独立 session 中执行, 避免与 caller 的 db 事务冲突.
        返回新行的 id.
        """
        from sqlalchemy.dialects.mysql import insert as mysql_insert
        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(
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
            await db.commit()
            pk = result.inserted_primary_key
            return pk[0] if pk else None

    @staticmethod
    async def _rollback_pending_job(job_id: int) -> None:
        """回滚预创建的 PENDING 行 (投递失败时调用)"""
        from sqlalchemy import delete
        from app.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(TrainingJob).where(TrainingJob.id == job_id)
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to rollback pending job %s", job_id)

    # ============== 任务签名构造 ==============

    @staticmethod
    def build_task_kwargs(
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
    ) -> Dict[str, Any]:
        """按 task_type 构造 Celery 任务 kwargs

        - classification: timm 微调 (base_model=..., 支持增量权重)
        - detection:     ultralytics YOLO (model_name=权重名, model_alias=落盘名)
        - segmentation:  torchvision DeepLabV3+ (backbone=..., model_alias=落盘名)
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
        # classification
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

    @staticmethod
    def _apply_training_task(task_type: str, kwargs: Dict[str, Any], task_id: str):
        """按 task_type 选 Celery 任务并投递 (强制使用预生成 task_id)"""
        if task_type == "detection":
            from app.workers.detection_tasks import train_detection_task
            task = train_detection_task
        elif task_type == "segmentation":
            from app.workers.segmentation_tasks import train_segmentation_task
            task = train_segmentation_task
        else:
            from app.workers.tasks import train_model_task
            task = train_model_task
        return task.apply_async(kwargs=kwargs, task_id=task_id)


__all__ = ["TrainingService"]
