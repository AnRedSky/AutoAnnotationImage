"""
TrainingLifecycleService — 训练任务生命周期服务
============================================

**v3.0.0 Phase 5 新增**: 抽取 3 个 worker 文件 (tasks / detection_tasks /
segmentation_tasks) 的共同编排逻辑, 消除 ~600 行重复代码.

**职责**:
- TrainingJob 创建/重置/状态机推进 (PROGRESS/SUCCESS/FAILURE/PAUSED)
- 训练历史累积 (Redis + DB 双写)
- 数据集统计 sticky_meta 持久化
- ModelVersion 创建
- 失败清理 (ModelVersion 记录 + 磁盘 .pth 清理)

**与已有服务的关系**:
- TrainingService: 负责"启动" (start / submit) — 已经在 Phase 3 落地
- JobStateService: 负责"查询" (snapshot) — 已经在 Phase 3 落地
- TrainingLifecycleService: 负责"执行" (worker 内部的状态机) — Phase 5 新增

**v3.0.0 Phase 5 设计**:
Worker 文件从 ~2000 行混合业务降到 ~200 行薄壳, 业务编排全部下沉到本服务.
ML 模块保持纯计算, 数据访问通过 service 注入, 不再散落在 worker 函数内.

**与原 tasks.py 模块级函数的关系 (兼容垫片)**:
- `_update_training_history` → TrainingLifecycleService.push_history
- `_persist_dataset_stats` → TrainingLifecycleService.persist_dataset_stats
- 兼容垫片保留, 旧代码不会立即断, Phase 5.6 清理.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.utils.async_helpers import run_async_in_worker as _run_async
from app.core.redis_client import redis_client
from app.config import settings

logger = logging.getLogger(__name__)


# ============== 跨 worker 共享的 sticky_meta ==============
# v2.5.28 引入: 失败路径 (_finish_failed_job) 也能拿到已计算的数据集统计
# 模块级 dict 跨函数共享, 简单够用 (每个 task_id 同时只有一个 worker 跑)
_LAST_STICKY_META: Dict[str, Dict[str, Any]] = {}


class TrainingLifecycleService:
    """训练任务生命周期服务 (无状态, 静态方法)

    所有方法都是 worker 可直接调用的同步 API (内部用 _run_async 切到事件循环).
    """

    # ============== 1. TrainingJob 创建/重置 ==============

    @staticmethod
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
        from app.model.training_job import TrainingJob

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

    @staticmethod
    def create_or_reset_job_sync(**kwargs) -> int:
        """同步包装: worker 进程直接调, 内部切到事件循环"""
        return _run_async(TrainingLifecycleService.create_or_reset_job(**kwargs))

    # ============== 2. 进度更新 (DB + Celery state) ==============

    @staticmethod
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
        from app.model.training_job import TrainingJob

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

    @staticmethod
    def update_job_progress_sync(**kwargs) -> None:
        """同步包装"""
        _run_async(TrainingLifecycleService.update_job_progress(**kwargs))

    # ============== 3. 训练历史推送 (Redis + DB) ==============

    @staticmethod
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
            TrainingLifecycleService.update_job_progress_sync(
                job_id=job_id,
                progress=progress or 0.0,
                message=message,
                current_epoch=current_epoch,
                history=history_buffer,
            )

    # ============== 4. sticky_meta 持久化 (数据集统计) ==============

    @staticmethod
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
            from app.model.training_job import TrainingJob

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

    @staticmethod
    def persist_dataset_stats_sync(task_id: str, extra: Dict[str, Any]) -> None:
        """同步包装"""
        _run_async(TrainingLifecycleService.persist_dataset_stats(task_id, extra))

    # ============== 5. 训练成功 (SUCCESS 状态) ==============

    @staticmethod
    async def mark_success(
        job_id: int,
        *,
        started_at: datetime,
        history_buffer: List[Dict[str, Any]],
        message: str,
        model_version_id: Optional[int] = None,
        sticky_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """写 DB SUCCESS + finished_at + duration + history + sticky_meta

        Args:
            job_id: TrainingJob.id
            started_at: 训练启动时间
            history_buffer: 训练历史曲线
            message: 状态描述 (e.g. "Training completed" / "mIoU=0.85")
            model_version_id: 关联的 ModelVersion.id (可选)
            sticky_meta: 数据集统计 (5 字段)
        """
        from app.database import AsyncSessionLocal
        from app.model.training_job import TrainingJob

        try:
            async with AsyncSessionLocal() as db:
                job = await db.get(TrainingJob, job_id)
                if not job:
                    return
                job.state = "SUCCESS"
                job.progress = 100.0
                job.message = message
                finished_at = datetime.utcnow()
                job.finished_at = finished_at
                job.duration_seconds = (finished_at - started_at).total_seconds()
                if history_buffer:
                    job.history = list(history_buffer)
                if model_version_id is not None:
                    job.model_version_id = model_version_id
                if sticky_meta:
                    if "data_total" in sticky_meta:
                        job.data_total = sticky_meta["data_total"]
                    if "data_train" in sticky_meta:
                        job.data_train = sticky_meta["data_train"]
                    if "data_val" in sticky_meta:
                        job.data_val = sticky_meta["data_val"]
                    if "num_classes" in sticky_meta:
                        job.num_classes = sticky_meta["num_classes"]
                    if "class_names" in sticky_meta:
                        job.class_names = sticky_meta["class_names"]
                await db.commit()
        except Exception as e:
            logger.warning("mark_success failed for job %s: %s", job_id, e)

    @staticmethod
    def mark_success_sync(**kwargs) -> None:
        """同步包装"""
        _run_async(TrainingLifecycleService.mark_success(**kwargs))

    # ============== 6. 训练失败 (FAILURE 状态) ==============

    @staticmethod
    async def mark_failure(
        job_id: int,
        *,
        error: str,
        started_at: datetime,
        exc_type: str = "UnknownError",
        sticky_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """写 DB FAILURE + finished_at + duration + error + sticky_meta

        兼容老 _finish_failed_job: 失败时也要保留已计算的数据集统计
        """
        from app.database import AsyncSessionLocal
        from app.model.training_job import TrainingJob

        try:
            async with AsyncSessionLocal() as db:
                job = await db.get(TrainingJob, job_id)
                if not job:
                    return
                job.state = "FAILURE"
                job.error = str(error)[:500]
                finished_at = datetime.utcnow()
                job.finished_at = finished_at
                job.duration_seconds = (finished_at - started_at).total_seconds()
                # v2.5.28: 失败也写 sticky_meta (来自 _LAST_STICKY_META 跨函数透传)
                if sticky_meta:
                    if "data_total" in sticky_meta:
                        job.data_total = sticky_meta["data_total"]
                    if "data_train" in sticky_meta:
                        job.data_train = sticky_meta["data_train"]
                    if "data_val" in sticky_meta:
                        job.data_val = sticky_meta["data_val"]
                    if "num_classes" in sticky_meta:
                        job.num_classes = sticky_meta["num_classes"]
                    if "class_names" in sticky_meta:
                        job.class_names = sticky_meta["class_names"]
                await db.commit()
        except Exception as e:
            logger.warning("mark_failure failed for job %s: %s", job_id, e)

    @staticmethod
    def mark_failure_sync(
        job_id: int,
        *,
        error: str,
        started_at: datetime,
        exc_type: str = "UnknownError",
        sticky_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """同步包装"""
        _run_async(TrainingLifecycleService.mark_failure(
            job_id,
            error=error,
            started_at=started_at,
            exc_type=exc_type,
            sticky_meta=sticky_meta,
        ))

    # ============== 7. 训练暂停 (PAUSED 状态) ==============

    @staticmethod
    async def mark_paused(
        job_id: int,
        *,
        started_at: datetime,
        epoch: int,
        total_epochs: int,
        history_buffer: List[Dict[str, Any]],
        model_name: str,
    ) -> None:
        """写 DB PAUSED + 清理半成品 ModelVersion + 删磁盘 .pth

        与 mark_failure 类似但保留 history (用户重训练时可恢复)
        """
        from sqlalchemy import delete
        from app.database import AsyncSessionLocal
        from app.model.training_job import TrainingJob
        from app.model.model_version import ModelVersion

        # ---- 1) 清理半成品 ModelVersion ----
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(ModelVersion).where(
                        ModelVersion.name == model_name,
                        ModelVersion.is_active == False,  # noqa: E712
                    )
                )
                await db.commit()
        except Exception as e:
            logger.warning("mark_paused cleanup ModelVersion failed: %s", e)

        # ---- 2) 删磁盘 .pth ----
        pth_path = settings.MODEL_DIR / f"{model_name}_best.pth"
        if pth_path.exists():
            try:
                pth_path.unlink()
            except OSError as e:
                logger.warning("mark_paused delete .pth failed: %s", e)

        # ---- 3) 写 DB PAUSED ----
        try:
            async with AsyncSessionLocal() as db:
                job = await db.get(TrainingJob, job_id)
                if job:
                    job.state = "PAUSED"
                    job.progress = round(epoch / max(total_epochs, 1) * 100, 2)
                    job.message = f"Paused at epoch {epoch}/{total_epochs}"
                    finished_at = datetime.utcnow()
                    job.finished_at = finished_at
                    job.duration_seconds = (finished_at - started_at).total_seconds()
                    if history_buffer:
                        job.history = list(history_buffer)
                    await db.commit()
        except Exception as e:
            logger.warning("mark_paused DB write failed: %s", e)

    @staticmethod
    def mark_paused_sync(**kwargs) -> None:
        """同步包装"""
        _run_async(TrainingLifecycleService.mark_paused(**kwargs))

    # ============== 8. ModelVersion 创建 ==============

    @staticmethod
    async def create_model_version(
        *,
        name: str,
        base_model: str,
        dataset_id: int,
        task_type: str,
        num_classes: int,
        file_path: str,
        metrics: Dict[str, Any],
        history: List[Dict[str, Any]],
        is_active: bool = False,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> int:
        """创建 ModelVersion 行 (training 成功时)

        Args:
            metrics: 训练指标 (map_50/map_50_95/precision/recall/miou/pixel_accuracy)
            history: 训练历史曲线
            extra_fields: 额外字段 (segmentation 用 miou/pixel_accuracy)

        Returns:
            ModelVersion.id
        """
        from app.database import AsyncSessionLocal
        from app.model.model_version import ModelVersion

        mv_data: Dict[str, Any] = {
            "name": name,
            "base_model": base_model,
            "dataset_id": dataset_id,
            "task_type": task_type,
            "num_classes": num_classes,
            "file_path": file_path,
            "is_active": is_active,
        }
        # 训练指标 (按 task_type 兼容)
        if task_type == "detection":
            mv_data["map_50"] = metrics.get("map_50")
            mv_data["map_50_95"] = metrics.get("map_50_95")
            mv_data["precision"] = metrics.get("precision")
            mv_data["recall"] = metrics.get("recall")
            mv_data["training_log"] = {"history": history}
        elif task_type == "segmentation":
            mv_data["miou"] = metrics.get("best_miou") or metrics.get("miou")
            mv_data["pixel_accuracy"] = metrics.get("best_pix_acc") or metrics.get("pixel_accuracy")

        if extra_fields:
            mv_data.update(extra_fields)

        async with AsyncSessionLocal() as db:
            mv = ModelVersion(**mv_data)
            db.add(mv)
            await db.commit()
            await db.refresh(mv)
            return mv.id

    @staticmethod
    def create_model_version_sync(**kwargs) -> int:
        """同步包装"""
        return _run_async(TrainingLifecycleService.create_model_version(**kwargs))

    # ============== 9. Celery state 推送工具 ==============

    @staticmethod
    def set_task_state(
        celery_task: Any,
        state: str,
        meta: Dict[str, Any],
    ) -> None:
        """统一的 Celery update_state, FAILURE 必须带 exc_type

        Args:
            celery_task: Celery task 实例 (self)
            state: PROGRESS / SUCCESS / FAILURE / REVOKED
            meta: 推送给前端的 meta dict
        """
        if state == "FAILURE" and "exc_type" not in meta:
            meta["exc_type"] = "UnknownError"
        try:
            celery_task.update_state(state=state, meta=meta)
        except Exception as e:
            logger.warning("set_task_state(%s) failed: %s", state, e)

    # ============== 10. sticky_meta 跨函数透传 (兼容旧用法) ==============

    @staticmethod
    def set_last_sticky_meta(task_id: str, sticky_meta: Dict[str, Any]) -> None:
        """记录最后一次的 sticky_meta (失败路径也能拿到)"""
        _LAST_STICKY_META[task_id] = dict(sticky_meta)

    @staticmethod
    def get_last_sticky_meta(task_id: str) -> Dict[str, Any]:
        """读取上次记录的 sticky_meta"""
        return _LAST_STICKY_META.get(task_id, {})


__all__ = [
    "TrainingLifecycleService",
    # 兼容旧 import 路径 (Phase 5.6 清理)
    "_update_training_history",
    "_persist_dataset_stats",
]


# ============== 兼容垫片 (Phase 5.6 清理) ==============
# 旧 worker 代码 (Phase 5 重构前的 tasks.py) 使用模块级函数,
# 保留这两个包装函数避免破坏, 内部委托给 TrainingLifecycleService.

def _update_training_history(task_id: str, history: list) -> None:
    """兼容垫片: 旧 tasks.py 模块级函数, 委托给 TrainingLifecycleService.push_history"""
    TrainingLifecycleService.push_history(task_id, history)


def _persist_dataset_stats(task_id: str, extra: dict) -> None:
    """兼容垫片: 旧 tasks.py 模块级函数, 委托给 TrainingLifecycleService"""
    TrainingLifecycleService.persist_dataset_stats_sync(task_id, extra)
