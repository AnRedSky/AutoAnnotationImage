"""
workers.segmentation.auto_annotate 模块 — 图像分割自动标注
========================================================

**v3.0.0 Phase S6 拆分**: 从 workers/segmentation.py 抽离
**职责**:
- auto_annotate_segmentation_task (用已训练 DeepLabV3+ 批量预标注, 写 SegmentationMask)

**Celery 字符串路径**: `app.tasks.workers.segmentation:auto_annotate_segmentation_task`
注: Phase S6 拆分后, 物理位置在 auto_annotate.py, 但 __init__.py 重新导出,
保持字符串路径和外部 import 完全向后兼容.

**流程** (与 train 不同, 不写 TrainingJob, 仅 Celery state):
1. 加载 ModelVersion + state_dict (从磁盘读 .pt)
2. load_model — 还原 DeepLabV3+
3. 加载图片 (worker 内联, 数据访问)
4. predict_to_mask_image — 推理 (纯 ML)
5. save_mask_pil — 保存 mask PNG 到存储
6. 写库 SegmentationMask (新行 / 更新旧行)
7. SUCCESS / FAILURE 状态推送

**注**: 本文件不包含 _finish_failed, 失败时使用 train.py 中的 _finish_failed
(已通过 __init__.py 重新导出, 但 auto_annotate 内部不用, 失败时直接 set_task_state).
"""
from __future__ import annotations

import os
from pathlib import Path

from app.tasks.workers.celery_app import celery_app
from app.utils.async_helpers import run_async_in_worker as _run_async

# 早期: 与 train.py 一致的 HF symlink + 缓存目录兜底
_model_dir_env = os.getenv("MODEL_DIR", "./models")
_cache_dir_env = os.getenv("PRETRAINED_CACHE_DIR", str(Path(_model_dir_env) / "cache"))
os.environ.setdefault("HF_HOME", str(Path(_cache_dir_env) / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(Path(_cache_dir_env) / "torch"))
os.environ.setdefault("ULTRALYTICS_HOME", str(Path(_cache_dir_env) / "ultralytics"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")


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
    from app.tasks.ml.segmentation.seg_predict import (
        load_model, predict_to_mask_image, save_mask_pil,
    )
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    task_id = self.request.id

    # ---- 1) 加载 ModelVersion + state_dict ----
    async def _load_mv():
        from app.database import AsyncSessionLocal
        from app.tasks.model.model_version import ModelVersion
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
        from app.tasks.model.image import Image as ImageModel
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
    from app.common.storage.storage_service import storage_service
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
    from app.annotation.model.segmentation_mask import SegmentationMask

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
