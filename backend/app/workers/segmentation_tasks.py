"""
Celery Tasks: 图像分割训练 + 自动标注 (v2.0.0 S5.2)
====================================================

- train_segmentation_task:        训练 DeepLabV3+ (复用 S5.1 ml/segmentation/seg_train)
- auto_annotate_segmentation_task: 用已训练模型批量预测, 结果入库 SegmentationMask

设计:
- 沿用 detection_tasks._run_async 风格, 自带事件循环管理
- 沿用 TrainingJob ORM, task_type='segmentation' 区分
- 训练/推理失败按 Celery 标准: update_state(FAILURE) + meta 必带 exc_type
"""
from __future__ import annotations

import os
import io
from datetime import datetime
from pathlib import Path
from typing import Optional
from sqlalchemy import select

from app.workers.celery_app import celery_app
from app.core.celery_utils import run_async_in_worker as _run_async


# 早期: 与 detection_tasks 一致的 HF symlink + 缓存目录兜底
# 在 import huggingface_hub / torchvision 前设置预训练权重缓存目录
_model_dir_env = os.getenv("MODEL_DIR", "./models")
_cache_dir_env = os.getenv("PRETRAINED_CACHE_DIR", str(Path(_model_dir_env) / "cache"))
os.environ.setdefault("HF_HOME", str(Path(_cache_dir_env) / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(Path(_cache_dir_env) / "torch"))
os.environ.setdefault("ULTRALYTICS_HOME", str(Path(_cache_dir_env) / "ultralytics"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from app.config import settings


def _set_task_state(self, state: str, meta: dict):
    if state == "FAILURE" and "exc_type" not in meta:
        meta = {**meta, "exc_type": "Exception", "exc_message": meta.get("error", "unknown")}
    try:
        self.update_state(state=state, meta=meta)
    except Exception:
        pass


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
    """
    启动分割训练 (DeepLabV3+):
    1) 创建/复用 TrainingJob (task_type='segmentation')
    2) 加载 (image, mask) pairs
    3) 调 train_segmentation (CPU, 1-3 epoch demo)
    4) 写 ModelVersion (state_dict 落盘)
    5) TrainingJob -> SUCCESS
    """
    from app.database import AsyncSessionLocal
    from app.models.training_job import TrainingJob
    from app.models.model_version import ModelVersion
    from app.models.dataset import Dataset
    from app.ml.segmentation.seg_dataset import collect_segmentation_pairs
    from app.ml.segmentation.seg_train import train_segmentation

    # 1) 建 TrainingJob
    async def _create_job():
        async with AsyncSessionLocal() as db:
            existing = (await db.execute(
                select(TrainingJob).where(
                    TrainingJob.celery_task_id == self.request.id
                )
            )).scalar_one_or_none()
            if existing:
                existing.state = "PROGRESS"
                existing.task_type = "segmentation"
                await db.commit()
                return existing.id
            job = TrainingJob(
                celery_task_id=self.request.id,
                user_id=user_id, dataset_id=dataset_id,
                base_model=backbone, model_name=model_alias,
                task_type="segmentation",
                epochs=epochs, batch_size=batch_size,
                state="PROGRESS",
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job.id

    job_id = _run_async(_create_job())

    # 2) 加载数据
    async def _load_pairs():
        async with AsyncSessionLocal() as db:
            return await collect_segmentation_pairs(db, dataset_id)
    images, masks = _run_async(_load_pairs())
    if not images:
        _set_task_state(self, "FAILURE", {
            "error": "数据集无 image+mask 配对, 请先上传 mask",
        })
        return {"status": "FAILURE", "reason": "empty_dataset"}

    # 3) 调进度回调
    def _train_cb(stage, current, total, info):
        progress = (current / total) if total else 0.0
        _set_task_state(self, "PROGRESS", {
            "stage": stage, "progress": progress,
            "current": current, "total": total, "info": info,
        })
        async def _update_job():
            async with AsyncSessionLocal() as db:
                j = await db.get(TrainingJob, job_id)
                if not j:
                    return
                j.progress = progress
                j.message = f"{stage} {current}/{total} {info}"
                j.current_epoch = current
                await db.commit()
        _run_async(_update_job())

    # 4) 训练
    try:
        # num_classes: dataset 类别数 + 1 (背景 0)
        async def _count_classes():
            async with AsyncSessionLocal() as db:
                from app.models.category import Category
                rows = (await db.execute(
                    select(Category).where(Category.dataset_id == dataset_id)
                )).scalars().all()
                return len(rows)
        n_cat = _run_async(_count_classes())
        num_classes = max(2, n_cat + 1)  # 至少 bg + 1

        result = train_segmentation(
            images=images, masks=masks,
            backbone=backbone, num_classes=num_classes,
            epochs=epochs, batch_size=batch_size,
            learning_rate=learning_rate, crop_size=crop_size,
            device=device, progress_cb=_train_cb,
        )
    except Exception as e:
        _set_task_state(self, "FAILURE", {
            "error": f"训练失败: {e}", "exc_type": type(e).__name__,
        })
        return {"status": "FAILURE", "error": str(e)}

    # 5) 落盘 ModelVersion
    weights_dir = settings.MODEL_DIR / "seg_runs"
    weights_dir.mkdir(parents=True, exist_ok=True)
    weights_path = weights_dir / f"{model_alias}_{self.request.id}.pt"
    if result.get("state_dict_bytes"):
        weights_path.write_bytes(result["state_dict_bytes"])

    async def _finish():
        async with AsyncSessionLocal() as db:
            mv = ModelVersion(
                name=model_alias, base_model=backbone,
                dataset_id=dataset_id, task_type="segmentation",
                num_classes=num_classes,
                file_path=str(weights_path.resolve()),
                miou=result["best_miou"],
                pixel_accuracy=result["best_pix_acc"],
            )
            db.add(mv)
            await db.commit()
            await db.refresh(mv)
            j = await db.get(TrainingJob, job_id)
            j.state = "SUCCESS"
            j.progress = 1.0
            j.message = f"mIoU={result['best_miou']:.4f}"
            j.model_version_id = mv.id
            j.duration_seconds = int(result["duration_seconds"])
            await db.commit()
            return mv.id

    mv_id = _run_async(_finish())
    _set_task_state(self, "SUCCESS", {
        "model_version_id": mv_id, "best_miou": result["best_miou"],
    })
    return {
        "status": "SUCCESS", "model_version_id": mv_id,
        "best_miou": result["best_miou"],
    }


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
    """
    用训练好的分割模型对 dataset 全部图跑推理, 写 SegmentationMask.
    """
    from app.database import AsyncSessionLocal
    from app.models.model_version import ModelVersion
    from app.models.image import Image as ImageModel
    from app.models.segmentation_mask import SegmentationMask
    from app.ml.segmentation.seg_predict import (
        load_model, predict_to_mask_image, save_mask_pil,
    )
    from app.services.storage_service import storage_service

    # 1) 加载 ModelVersion + state_dict
    async def _load_mv():
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
        _set_task_state(self, "FAILURE", {"error": "ModelVersion 不可用"})
        return {"status": "FAILURE"}

    model = load_model(
        state_bytes, backbone=mv.base_model,
        num_classes=mv.num_classes, device=device,
    )

    # 2) 拉图
    async def _load_images():
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(ImageModel).where(
                    ImageModel.dataset_id == dataset_id,
                    ImageModel.task_type == "segmentation",
                )
            )).scalars().all()
            return rows

    images = _run_async(_load_images())
    if not images:
        _set_task_state(self, "FAILURE", {"error": "dataset 无 segmentation 图"})
        return {"status": "FAILURE"}

    # 3) 推理
    base = Path(storage_service.base_dir)
    image_paths = [str((base / im.storage_path).resolve()) for im in images]
    try:
        mask_map = predict_to_mask_image(
            model, image_paths, crop_size=crop_size, device=device,
        )
    except Exception as e:
        _set_task_state(self, "FAILURE", {
            "error": f"推理失败: {e}", "exc_type": type(e).__name__,
        })
        return {"status": "FAILURE"}

    # 4) 写库
    saved = 0
    skipped = 0
    for im, abs_p in zip(images, image_paths):
        try:
            pil_mask = mask_map.get(abs_p)
            if pil_mask is None:
                skipped += 1
                continue

            async def _save_mask(image_id=im.id, dataset_id_=im.dataset_id,
                                 pil=pil_mask):
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

    _set_task_state(self, "SUCCESS", {
        "saved": saved, "skipped": skipped, "total": len(images),
    })
    return {"status": "SUCCESS", "saved": saved, "skipped": skipped}
