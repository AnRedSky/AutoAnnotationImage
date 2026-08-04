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

from app.core.config import settings
from app.tasks.model.dataset import Dataset
from app.tasks.model.training_job import TrainingJob

logger = logging.getLogger(__name__)


# 任务签名分发表 (3 种任务类型 → Celery 函数 + kwargs 构造)
_TASK_DISPATCH: Dict[str, str] = {
    "classification": "app.tasks.workers.classification:train_model_task",
    "detection": "app.tasks.workers.detection:train_detection_task",
    "segmentation": "app.tasks.workers.segmentation:train_segmentation_task",
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

        # model_name 兜底 + 长度保护
        # v3.0.0 改版:
        #   1) 用户没传 → 自动按 `{base_model}_{ts}` 生成 (去掉旧 `_v1_` 中缀)
        #   2) 用户传了 → 尊重用户输入, 但仍截断到 128 字符
        #   3) 查重: 自动生成时, 若 dataset 下同名已存在, 追加 3 位随机数字 (e.g. _847)
        from app.tasks.api.training.start import (
            _default_model_name,
            _resolve_unique_model_name_async,
            _safe_truncate_model_name,
        )
        from sqlalchemy import and_ as _and, exists as _sa_exists, select as _sa_select
        from app.tasks.model.training_job import TrainingJob as TJ

        user_provided = bool(model_name and model_name.strip())
        if not user_provided:
            # 1) 自动生成默认名
            candidate = _default_model_name(base_model, retrain=False)
            # 2) 查重 (同一 dataset 下重名时, 自动加 3 位随机后缀)
            async def _exists(name: str) -> bool:
                stmt = _sa_select(_sa_exists().where(
                    _and(
                        TJ.dataset_id == dataset_id,
                        TJ.model_name == name,
                    )
                ))
                return bool((await db.execute(stmt)).scalar())
            model_name = await _resolve_unique_model_name_async(candidate, _exists)
        else:
            # 用户输入: 截断兜底, 不做查重 (用户主动选择同名, 视为合法)
            model_name = model_name.strip()
            model_name = _safe_truncate_model_name(model_name)

        # v3.0.0: 决定 pretrain_mode
        # - 用户手动传了 .pth (pretrained_model_path 非空): incremental, 但不关联业务 MV
        #   (这是用户私有 .pth, 不在我们的 ModelVersion 体系内, source_mv_id 留空)
        # - 否则: from_scratch (基于 timm ImageNet 预训练权重)
        from app.tasks.model.training_job import (
            PRETRAIN_MODE_FROM_SCRATCH,
            PRETRAIN_MODE_INCREMENTAL,
        )
        pretrain_mode = (
            PRETRAIN_MODE_INCREMENTAL if (pretrained_model_path and pretrained_model_path.strip())
            else PRETRAIN_MODE_FROM_SCRATCH
        )

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
            pretrain_mode=pretrain_mode,
            pretrain_source_mv_id=None,  # 全新训练入口无业务 MV 关联
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
        pretrain_mode: Optional[str] = None,
        pretrain_source_mv_id: Optional[int] = None,
    ) -> int:
        """预创建 TrainingJob 行 (state=PENDING)

        必须在独立 session 中执行, 避免与 caller 的 db 事务冲突.
        返回新行的 id.

        v3.0.0: 新增 pretrain_mode + pretrain_source_mv_id, 用于详情页追溯
        "这个任务是基于 timm ImageNet 权重, 还是基于某条业务 MV 增量训练".
        start_training 显式传, start_existing 走 _create_pending_restart_job 另一份.
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
                    pretrain_mode=pretrain_mode,
                    pretrain_source_mv_id=pretrain_source_mv_id,
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
            from app.tasks.workers.detection import train_detection_task
            task = train_detection_task
        elif task_type == "segmentation":
            from app.tasks.workers.segmentation import train_segmentation_task
            task = train_segmentation_task
        else:
            from app.tasks.workers.classification import train_model_task
            task = train_model_task
        return task.apply_async(kwargs=kwargs, task_id=task_id)


__all__ = ["TrainingService"]
