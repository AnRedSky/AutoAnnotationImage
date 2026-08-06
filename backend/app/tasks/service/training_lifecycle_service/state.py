"""
training_lifecycle_service.state — 训练任务状态机 (SUCCESS / FAILURE / PAUSED)
============================================================================

**v3.0.0 Phase S5 拆分**: 从 training_lifecycle_service.py (580行) 抽离
**职责**:
- mark_success / _sync — 写 DB SUCCESS + finished_at + duration + history + sticky_meta
- mark_failure / _sync — 写 DB FAILURE + finished_at + duration + error + sticky_meta
- mark_paused / _sync — 写 DB PAUSED + 清理半成品 ModelVersion + 删磁盘 .pth

**状态机设计** (Phase 5):
- mark_success: SUCCESS + 100% + model_version_id 关联
- mark_failure: FAILURE + error 截断 500 字 + sticky_meta 跨函数透传
- mark_paused: PAUSED + 保留 history (供重训练恢复) + 清理半成品 MV
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.utils.async_helpers import run_async_in_worker as _run_async
from app.core.config import settings

logger = logging.getLogger(__name__)


# ============== 5. 训练成功 (SUCCESS 状态) ==============

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
    from app.tasks.model.training_job import TrainingJob

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
                # v3.0.0: 训练设备信息持久化 (由 worker 调 collect_device_info 塞入 sticky_meta)
                # - device_type: "cpu" / "cuda" / "mps"
                # - device_name: GPU 型号 / CPU 描述
                # - device_info: 完整 dict (cuda_version / gpu_memory / cpu_count / ram 等)
                # - gpu_peak_memory_mb: CUDA 时的峰值显存 (由训练过程监控更新)
                if "device_type" in sticky_meta:
                    job.device_type = sticky_meta["device_type"]
                if "device_name" in sticky_meta:
                    job.device_name = sticky_meta["device_name"]
                if "device_info" in sticky_meta:
                    job.device_info = sticky_meta["device_info"]
                if "gpu_peak_memory_mb" in sticky_meta and sticky_meta["gpu_peak_memory_mb"] is not None:
                    job.gpu_peak_memory_mb = sticky_meta["gpu_peak_memory_mb"]
            await db.commit()
    except Exception as e:
        logger.warning("mark_success failed for job %s: %s", job_id, e)


def mark_success_sync(**kwargs) -> None:
    """同步包装"""
    _run_async(mark_success(**kwargs))


# ============== 6. 训练失败 (FAILURE 状态) ==============

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
    from app.tasks.model.training_job import TrainingJob

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
                # v3.0.0: 失败任务也记录运行设备 (便于排查 OOM/CUDA 异常等设备相关问题)
                if "device_type" in sticky_meta:
                    job.device_type = sticky_meta["device_type"]
                if "device_name" in sticky_meta:
                    job.device_name = sticky_meta["device_name"]
                if "device_info" in sticky_meta:
                    job.device_info = sticky_meta["device_info"]
                if "gpu_peak_memory_mb" in sticky_meta and sticky_meta["gpu_peak_memory_mb"] is not None:
                    job.gpu_peak_memory_mb = sticky_meta["gpu_peak_memory_mb"]
            await db.commit()
    except Exception as e:
        logger.warning("mark_failure failed for job %s: %s", job_id, e)


def mark_failure_sync(
    job_id: int,
    *,
    error: str,
    started_at: datetime,
    exc_type: str = "UnknownError",
    sticky_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """同步包装"""
    _run_async(mark_failure(
        job_id,
        error=error,
        started_at=started_at,
        exc_type=exc_type,
        sticky_meta=sticky_meta,
    ))


# ============== 7. 训练暂停 (PAUSED 状态) ==============

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
    from app.tasks.model.training_job import TrainingJob
    from app.tasks.model.model_version import ModelVersion

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
    # v3.6.2 修复: 旧版路径用 settings.MODEL_DIR / f"{model_name}_best.pth"
    #   (路径错, 真实落盘点是 settings.CLASSIFICATION_MODEL_DIR, 实际从未删成功)
    # 新版: 按 task_type 解析正确路径, 与 worker 末尾 model_saver 写入路径一致
    # 实际作用: 用户点 "再训练" 时, 旧 model 的 .pth 会被清理, 避免堆积
    # 注: segmentation / detection 的 .pt 不在此处删 (它们走自己的 run 目录, 不会被复用)
    try:
        async with AsyncSessionLocal() as _db:
            _job_for_pause = await _db.get(TrainingJob, job_id)
            _tt = _job_for_pause.task_type if _job_for_pause else None
        if _tt == "classification":
            pth_path = settings.CLASSIFICATION_MODEL_DIR / f"{model_name}_best.pth"
            if pth_path.exists():
                pth_path.unlink()
        # segmentation / detection 不在此处清理 (路径含 task_id / 走 run 目录)
    except Exception as e:
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


def mark_paused_sync(**kwargs) -> None:
    """同步包装"""
    _run_async(mark_paused(**kwargs))


# ============== 8. 训练取消 (CANCELED 状态, v3.5.0) ==============

async def mark_canceled(
    job_id: int,
    *,
    started_at: datetime,
    epoch: int,
    total_epochs: int,
    history_buffer: List[Dict[str, Any]],
    model_name: str,
    reason: str = "user_cancel",
) -> None:
    """写 DB CANCELED + 清理半成品 ModelVersion (与 mark_paused 对称)

    区别于 mark_paused:
    - mark_paused 保留 history 供用户"继续" (DB state=PAUSED, 非终态)
    - mark_canceled 保留 history 但用户无法"继续" (DB state=CANCELED, 终态, 只能"再训练")

    资源清理:
    - 删 ModelVersion (与 mark_paused 策略一致: 删 WHERE name=model_name AND is_active=False)
    - **不删 .pt/.pth 文件** (mark_paused 现状的 .pth 路径对 detection 实际是错的, 避免引入新 bug;
      进程退出时由 OS 清理)
    - history 保留 (前端 SSE 展示已跑完 epoch)

    Args:
        job_id: TrainingJob.id
        started_at: 训练启动时间
        epoch: 取消时正在跑的 epoch (1-based)
        total_epochs: 总 epoch 数
        history_buffer: 训练历史曲线
        model_name: 模型名 (用于定位半成品 ModelVersion)
        reason: 取消原因 (默认 "user_cancel")
    """
    from sqlalchemy import delete
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob
    from app.tasks.model.model_version import ModelVersion

    # ---- 1) 清理半成品 ModelVersion (与 mark_paused 一致策略) ----
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
        logger.warning("mark_canceled cleanup ModelVersion failed: %s", e)

    # ---- 2) 写 DB CANCELED ----
    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(TrainingJob, job_id)
            if job:
                job.state = "CANCELED"
                job.progress = round(epoch / max(total_epochs, 1) * 100, 2)
                job.message = f"训练已取消 (epoch {epoch}/{total_epochs}, {reason})"
                finished_at = datetime.utcnow()
                job.finished_at = finished_at
                job.duration_seconds = (finished_at - started_at).total_seconds()
                # 解关联半成品 ModelVersion (与 mark_paused 不一样, 因为 mark_paused 走的是
                # SQL DELETE, 这里是显式置 NULL, 避免和 step 1 的 DELETE 重复)
                job.model_version_id = None
                if history_buffer:
                    job.history = list(history_buffer)
                await db.commit()
    except Exception as e:
        logger.warning("mark_canceled DB write failed: %s", e)


def mark_canceled_sync(
    job_id: int,
    *,
    started_at: datetime,
    epoch: int,
    total_epochs: int,
    history_buffer: List[Dict[str, Any]],
    model_name: str,
    reason: str = "user_cancel",
) -> None:
    """同步包装"""
    _run_async(mark_canceled(
        job_id=job_id,
        started_at=started_at,
        epoch=epoch,
        total_epochs=total_epochs,
        history_buffer=history_buffer,
        model_name=model_name,
        reason=reason,
    ))


__all__ = [
    "mark_success",
    "mark_success_sync",
    "mark_failure",
    "mark_failure_sync",
    "mark_paused",
    "mark_paused_sync",
    "mark_canceled",       # v3.5.0 新增
    "mark_canceled_sync",  # v3.5.0 新增
]
