"""
workers.segmentation.train 模块 — 图像分割训练任务
==================================================

**v3.0.0 Phase S6 拆分**: 从 workers/segmentation.py 抽离
**职责**: train_segmentation_task (DeepLabV3+ 异步训练) + _finish_failed 失败清理

**Celery 字符串路径**: `app.tasks.workers.segmentation:train_segmentation_task`
注意: Phase S6 拆分后, train_segmentation_task 物理位置在 train.py, 但 __init__.py 重新导出,
保持字符串路径和外部 import 完全向后兼容.

**训练流程** (Phase 5: 编排下沉到 TrainingLifecycleService):
1. TrainingLifecycleService.create_or_reset_job_sync — 创建/复用 TrainingJob
2. collect_segmentation_pairs — 加载 (image, mask) 配对
3. 计算 num_classes (类别数 + 背景 0)
4. 构造 sticky_meta (data_total/data_train/data_val/num_classes/class_names)
5. set_last_sticky_meta — 跨函数透传 sticky_meta (失败路径也能拿到)
6. train_segmentation — 纯 ML 训练 (DeepLabV3+)
7. 落盘 ModelVersion (.pt) + TrainingJob SUCCESS

**早期环境变量兜底** (与 detection/train.py 一致):
- HF_HOME / TORCH_HOME / ULTRALYTICS_HOME 相对路径
- HF_HUB_DISABLE_SYMLINKS / HF_HUB_DISABLE_SYMLINKS_WARNING
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from app.tasks.workers.celery_app import celery_app
from app.utils.async_helpers import run_async_in_worker as _run_async

# 早期: 与 detection/train.py 一致的 HF symlink + 缓存目录兜底
_model_dir_env = os.getenv("MODEL_DIR", "./models")
_cache_dir_env = os.getenv("PRETRAINED_CACHE_DIR", str(Path(_model_dir_env) / "cache"))
os.environ.setdefault("HF_HOME", str(Path(_cache_dir_env) / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(Path(_cache_dir_env) / "torch"))
os.environ.setdefault("ULTRALYTICS_HOME", str(Path(_cache_dir_env) / "ultralytics"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from app.core.config import settings  # noqa: E402

# v2.5.29: ultralytics 路径强制覆盖
from app.tasks.ml.ultralytics_setup import configure_ultralytics, migrate_legacy_yolo_weights  # noqa: E402
configure_ultralytics()
migrate_legacy_yolo_weights()


@celery_app.task(bind=True)
def train_segmentation_task(
    self,
    dataset_id: int,
    user_id: int,
    backbone: str = "deeplabv3_resnet50",
    model_alias: str = "deeplabv3_run",
    epochs: int = 3,
    batch_size: int = 4,
    crop_size: int = 256,
    learning_rate: float = 1e-4,
    device: str = "cpu",
):
    """启动分割训练 (DeepLabV3+, Phase 5: 编排下沉到 TrainingLifecycleService)"""
    from app.tasks.ml.segmentation.seg_dataset import collect_segmentation_pairs
    from app.tasks.ml.segmentation.seg_train import train_segmentation
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    task_id = self.request.id
    started_at = datetime.utcnow()

    # ---- 1) 创建/复用 TrainingJob (委托 Service) ----
    job_id = TrainingLifecycleService.create_or_reset_job_sync(
        task_id=task_id,
        user_id=user_id,
        dataset_id=dataset_id,
        base_model=backbone,
        model_name=model_alias,
        task_type="segmentation",
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        started_at=started_at,
    )

    # ---- 2) 加载 (image, mask) pairs ----
    async def _load_pairs():
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            return await collect_segmentation_pairs(db, dataset_id)
    images, masks = _run_async(_load_pairs())
    if not images:
        _finish_failed(self, job_id, "数据集无 image+mask 配对, 请先上传 mask", started_at, task_id)
        return {"status": "FAILURE", "reason": "empty_dataset"}

    # v3.0.0: 采集训练设备信息 (CPU/GPU/CUDA/显存), 塞入 sticky_meta 供 mark_success 持久化
    # - 尽早采集, 失败路径也能通过 sticky_meta 记录运行设备
    _device_info_dict: dict = {}
    try:
        from app.tasks.ml.device_info import collect_device_info, extract_job_device_fields
        _device_info_dict = collect_device_info()
    except Exception:
        pass

    # ---- 3) 加载类目 (用于 sticky_meta) ----
    async def _load_categories():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.tasks.model.category import Category
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(Category).where(Category.dataset_id == dataset_id)
            )).scalars().all()
            return [c.name for c in rows]
    category_names = _run_async(_load_categories())

    n_total = len(masks)
    n_train = int(n_total * 0.8)
    n_val = n_total - n_train
    sticky_meta: dict = {
        "data_total": n_total,
        "data_train": n_train,
        "data_val": n_val,
        "num_classes": None,
        "class_names": sorted(category_names),
    }
    # v3.0.0: 合并设备信息字段 (供 mark_success/mark_failure 持久化到 TrainingJob)
    if _device_info_dict:
        try:
            from app.tasks.ml.device_info import extract_job_device_fields
            sticky_meta.update(extract_job_device_fields(_device_info_dict))
        except Exception:
            pass
    # 跨函数透传 (失败路径也能拿到)
    TrainingLifecycleService.set_last_sticky_meta(task_id, sticky_meta)

    history_buffer: list = []

    def _train_cb(stage, current, total, info, metrics=None):
        progress_pct = (current / total * 100.0) if total else 0.0
        meta = {
            "stage": stage,
            "progress": round(progress_pct, 2),
            "current": current,
            "total": total,
            "current_epoch": current,
            "total_epochs": total,
            "msg": f"{stage} {current}/{total} {info}".strip(),
            "info": info,
        }
        if metrics and isinstance(metrics, dict):
            for _k in ("train_loss", "val_loss", "miou", "pixel_acc", "dice"):
                if _k in metrics and isinstance(metrics[_k], (int, float)):
                    meta[_k] = metrics[_k]
            history_buffer.append(metrics)
        meta.update(sticky_meta)
        TrainingLifecycleService.set_task_state(self, "PROGRESS", meta)
        # 推历史曲线
        if history_buffer:
            TrainingLifecycleService.push_history(
                task_id, list(history_buffer),
                job_id=job_id,
                progress=progress_pct,
                message=f"{stage} {current}/{total} {info}",
                current_epoch=current,
            )

    # ---- 4) 训练 ----
    try:
        # v3.1.0 Phase W3.1: 复用已加载的 category_names 计算 num_classes, 消除重复 DB 查询
        # 旧实现用 _count_classes() 又查了一次 Category 表 (与 L106-114 完全相同)
        n_cat = len(category_names)
        num_classes = max(2, n_cat + 1)
        sticky_meta["num_classes"] = num_classes
        TrainingLifecycleService.set_last_sticky_meta(task_id, sticky_meta)

        # 数据集就绪推送 + 写库
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": 0.0,
            "msg": f"数据集就绪: train={n_train} val={n_val} num_classes={num_classes}",
            "total_epochs": epochs,
            **sticky_meta,
        })
        TrainingLifecycleService.persist_dataset_stats_sync(task_id, sticky_meta, job_id=job_id)

        result = train_segmentation(
            images=images, masks=masks,
            backbone=backbone, num_classes=num_classes,
            epochs=epochs, batch_size=batch_size,
            learning_rate=learning_rate, crop_size=crop_size,
            device=device, progress_cb=_train_cb,
        )
    except Exception as e:
        _finish_failed(self, job_id, f"训练失败: {e}", started_at, task_id)
        return {"status": "FAILURE", "error": str(e)}

    # ---- 5) 落盘 ModelVersion + TrainingJob SUCCESS (委托 Service) ----
    weights_dir = settings.MODEL_DIR / "seg_runs"
    weights_dir.mkdir(parents=True, exist_ok=True)
    weights_path = weights_dir / f"{model_alias}_{task_id}.pt"
    if result.get("state_dict_bytes"):
        weights_path.write_bytes(result["state_dict_bytes"])

    mv_id = TrainingLifecycleService.create_model_version_sync(
        name=model_alias,
        base_model=backbone,
        dataset_id=dataset_id,
        task_type="segmentation",
        num_classes=num_classes,
        file_path=str(weights_path.resolve()),
        metrics={
            "best_miou": result["best_miou"],
            "best_pix_acc": result["best_pix_acc"],
        },
        history=history_buffer,
    )
    TrainingLifecycleService.mark_success_sync(
        job_id=job_id,
        started_at=started_at,
        history_buffer=history_buffer,
        message=f"mIoU={result['best_miou']:.4f}",
        model_version_id=mv_id,
        sticky_meta=sticky_meta,
    )
    TrainingLifecycleService.set_task_state(self, "SUCCESS", {
        "model_version_id": mv_id, "best_miou": result["best_miou"],
    })
    return {
        "status": "SUCCESS", "model_version_id": mv_id,
        "best_miou": result["best_miou"],
    }


def _finish_failed(self, job_id: int, error_msg: str, started_at: datetime, task_id: str):
    """训练失败统一清理 (Phase 5: 委托 TrainingLifecycleService)

    注: 此函数在 segmentation/train.py 和 segmentation/auto_annotate.py 之间共享,
    __init__.py 重新导出.
    """
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    sticky_meta = TrainingLifecycleService.get_last_sticky_meta(task_id)
    TrainingLifecycleService.mark_failure_sync(
        job_id=job_id,
        error=error_msg,
        started_at=started_at,
        exc_type="SegmentationTrainError",
        sticky_meta=sticky_meta,
    )
    TrainingLifecycleService.set_task_state(self, "FAILURE", {
        "exc_type": "SegmentationTrainError",
        "exc_message": error_msg[:200],
        "error": error_msg[:500],
        "job_id": job_id,
    })
