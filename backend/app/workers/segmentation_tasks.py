"""
Celery Tasks: 图像分割训练 + 自动标注 (v3.0.0 Phase 5 薄化)
====================================================

- train_segmentation_task:        训练 DeepLabV3+ (委托 TrainingLifecycleService)
- auto_annotate_segmentation_task: 用已训练模型批量预测, 结果入库 SegmentationMask

**v3.0.0 Phase 5 重构**:
- 业务编排 (TrainingJob 状态机 / sticky_meta / 历史推送 / 失败清理) 全部下沉到
  TrainingLifecycleService, worker 主体从 ~470 行减到 ~280 行
- ML 模块 (seg_train/seg_dataset/seg_predict) 保持纯计算
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from app.workers.celery_app import celery_app
from app.utils.async_helpers import run_async_in_worker as _run_async

# 早期: 与 detection_tasks 一致的 HF symlink + 缓存目录兜底
_model_dir_env = os.getenv("MODEL_DIR", "./models")
_cache_dir_env = os.getenv("PRETRAINED_CACHE_DIR", str(Path(_model_dir_env) / "cache"))
os.environ.setdefault("HF_HOME", str(Path(_cache_dir_env) / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(Path(_cache_dir_env) / "torch"))
os.environ.setdefault("ULTRALYTICS_HOME", str(Path(_cache_dir_env) / "ultralytics"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from app.config import settings  # noqa: E402

# v2.5.29: ultralytics 路径强制覆盖
from app.core.ultralytics_setup import configure_ultralytics, migrate_legacy_yolo_weights  # noqa: E402
configure_ultralytics()
migrate_legacy_yolo_weights()


# ============== 训练 ==============

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
    from app.ml.segmentation.seg_dataset import collect_segmentation_pairs
    from app.ml.segmentation.seg_train import train_segmentation
    from app.services import TrainingLifecycleService

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

    # ---- 3) 加载类目 (用于 sticky_meta) ----
    async def _load_categories():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.model.category import Category
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
        # 计算 num_classes (类别数 + 背景 0)
        async def _count_classes():
            from sqlalchemy import select
            from app.database import AsyncSessionLocal
            from app.model.category import Category
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(Category).where(Category.dataset_id == dataset_id)
                )).scalars().all()
                return len(rows)
        n_cat = _run_async(_count_classes())
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
        TrainingLifecycleService.persist_dataset_stats_sync(task_id, sticky_meta)

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
    """训练失败统一清理 (Phase 5: 委托 TrainingLifecycleService)"""
    from app.services import TrainingLifecycleService

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


# ============== 自动标注 ==============

@celery_app.task(bind=True)
def auto_annotate_segmentation_task(
    self,
    dataset_id: int,
    user_id: int,
    model_version_id: int,
    overwrite_existing: bool = False,
    crop_size: int = 256,
    device: str = "cpu",
):
    """用训练好的分割模型对 dataset 全部图跑推理, 写 SegmentationMask (Phase 5: 编排下沉)"""
    from app.ml.segmentation.seg_predict import (
        load_model, predict_to_mask_image, save_mask_pil,
    )
    from app.services import TrainingLifecycleService

    task_id = self.request.id

    # ---- 1) 加载 ModelVersion + state_dict ----
    async def _load_mv():
        from app.database import AsyncSessionLocal
        from app.model.model_version import ModelVersion
        async with AsyncSessionLocal() as db:
            mv = await db.get(ModelVersion, model_version_id)
            if not mv or not mv.file_path:
                return None, None
            try:
                state_bytes = Path(mv.file_path).read_bytes()
            except Exception:
                state_bytes = None
            return mv, state_bytes

    mv, state_bytes = _run_async(_load_mv())
    if not mv or not state_bytes:
        TrainingLifecycleService.set_task_state(self, "FAILURE", {"error": "ModelVersion 不可用"})
        return {"status": "FAILURE"}

    model = load_model(
        state_bytes, backbone=mv.base_model,
        num_classes=mv.num_classes, device=device,
    )

    # ---- 2) 加载图片 ----
    async def _load_images():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.model.image import Image as ImageModel
        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(ImageModel).where(
                    ImageModel.dataset_id == dataset_id,
                    ImageModel.task_type == "segmentation",
                )
            )).scalars().all()
    images = _run_async(_load_images())
    if not images:
        TrainingLifecycleService.set_task_state(self, "FAILURE", {"error": "dataset 无 segmentation 图"})
        return {"status": "FAILURE"}

    # ---- 3) 推理 ----
    from app.services.storage_service import storage_service
    base = Path(storage_service.base_dir)
    image_paths = [str((base / im.storage_path).resolve()) for im in images]
    try:
        mask_map = predict_to_mask_image(
            model, image_paths, crop_size=crop_size, device=device,
        )
    except Exception as e:
        TrainingLifecycleService.set_task_state(self, "FAILURE", {
            "error": f"推理失败: {e}", "exc_type": type(e).__name__,
        })
        return {"status": "FAILURE"}

    # ---- 4) 写库 (worker 内联, 数据访问) ----
    import io
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.model.segmentation_mask import SegmentationMask

    saved = 0
    skipped = 0
    for im, abs_p in zip(images, image_paths):
        try:
            pil_mask = mask_map.get(abs_p)
            if pil_mask is None:
                skipped += 1
                continue

            async def _save_mask(image_id=im.id, dataset_id_=im.dataset_id, pil=pil_mask):
                async with AsyncSessionLocal() as db:
                    existing = (await db.execute(
                        select(SegmentationMask).where(
                            SegmentationMask.image_id == image_id,
                        )
                    )).scalar_one_or_none()
                    if existing and not overwrite_existing:
                        return False
                    storage_key = save_mask_pil(pil, dataset_id_, image_id)
                    full_path = base / storage_key
                    if not full_path.parent.is_dir():
                        full_path.parent.mkdir(parents=True, exist_ok=True)
                    if not full_path.exists():
                        buf = io.BytesIO()
                        pil.save(buf, format="PNG")
                        full_path.write_bytes(buf.getvalue())
                    if existing:
                        existing.mask_path = storage_key
                        existing.width = pil.size[0]
                        existing.height = pil.size[1]
                        existing.source = "ai"
                    else:
                        db.add(SegmentationMask(
                            image_id=image_id,
                            mask_path=storage_key,
                            width=pil.size[0],
                            height=pil.size[1],
                            source="ai",
                            annotated_by=user_id,
                        ))
                    await db.commit()
                    return True

            if _run_async(_save_mask()):
                saved += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
            continue

    TrainingLifecycleService.set_task_state(self, "SUCCESS", {
        "saved": saved, "skipped": skipped, "total": len(images),
    })
    return {"status": "SUCCESS", "saved": saved, "skipped": skipped}
