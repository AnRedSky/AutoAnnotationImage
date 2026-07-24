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

# ---- v2.5.29: ultralytics 路径强制覆盖 (与 detection_tasks.py / tasks.py 一致) ----
# 分割训练本身不用 YOLO, 但 ai_service 加载 timm 模型时若误用 ultralytics 也会
# 受影响; 统一在 worker 启动时锁路径, 避免 ultralytics 把 yolov8*.pt 落到 cwd.
from app.core.ultralytics_setup import configure_ultralytics, migrate_legacy_yolo_weights
configure_ultralytics()
migrate_legacy_yolo_weights()


def _set_task_state(self, state: str, meta: dict):
    if state == "FAILURE" and "exc_type" not in meta:
        meta = {**meta, "exc_type": "Exception", "exc_message": meta.get("error", "unknown")}
    try:
        self.update_state(state=state, meta=meta)
    except Exception:
        pass


# ============== 训练 ==============

def _finish_failed_job(self, job_id: int, error_msg: str, started_at: datetime):
    """
    v2.5.28 新增: 训练失败统一清理 (与 detection_tasks._finish_failed_job 风格一致)
    - 写 DB FAILURE + finished_at + duration_seconds + error
    - 推 Celery FAILURE state

    用于以下失败路径:
      a) 数据集空 (没 image+mask 配对)
      b) train_segmentation 抛异常 (OOB / 显存不足 / 模型加载失败等)
    """
    from app.database import AsyncSessionLocal
    from app.models.training_job import TrainingJob
    try:
        async def _fail():
            async with AsyncSessionLocal() as db:
                j = await db.get(TrainingJob, job_id)
                if j:
                    finished_at = datetime.utcnow()
                    j.state = "FAILURE"
                    j.finished_at = finished_at
                    j.duration_seconds = (finished_at - started_at).total_seconds()
                    j.error = error_msg[:500]
                    await db.commit()
        _run_async(_fail())
    except Exception:
        # 写库失败也不阻塞 Celery 状态推送
        pass
    _set_task_state(self, "FAILURE", {
        "exc_type": "SegmentationTrainError",
        "exc_message": error_msg[:200],
        "error": error_msg[:500],
        "job_id": job_id,
    })


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
    3) 调 train_segmentation (CPU, 1-3 epoch 快速验证)
    4) 写 ModelVersion (state_dict 落盘)
    5) TrainingJob -> SUCCESS
    """
    from app.database import AsyncSessionLocal
    from app.models.training_job import TrainingJob
    from app.models.model_version import ModelVersion
    from app.models.dataset import Dataset
    from app.ml.segmentation.seg_dataset import collect_segmentation_pairs
    from app.ml.segmentation.seg_train import train_segmentation

    # v2.5.28 修复: 之前分割任务完全没写 started_at / finished_at / duration_seconds,
    # 导致详情页"开始时间/结束时间/耗时"全显示 '-'. 现在按 tasks.py / detection_tasks.py
    # 的约定, 在 worker 接手时记 started_at, 终态时记 finished_at + duration_seconds
    task_id = self.request.id
    started_at = datetime.utcnow()

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
                existing.progress = 0.0
                existing.error = None
                # v2.5.28: 重投递 / API 预创建 都重置时间字段
                existing.started_at = started_at
                existing.finished_at = None
                existing.duration_seconds = None
                await db.commit()
                return existing.id
            job = TrainingJob(
                celery_task_id=self.request.id,
                user_id=user_id, dataset_id=dataset_id,
                base_model=backbone, model_name=model_alias,
                task_type="segmentation",
                epochs=epochs, batch_size=batch_size,
                state="PROGRESS",
                progress=0.0,
                started_at=started_at,  # v2.5.28: worker 接手时立即记
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
        # v2.5.28 修复: 失败也写 DB (finished_at + duration_seconds + error),
        # 否则详情页"结束时间/耗时"永远是空
        _finish_failed_job(self, job_id, "数据集无 image+mask 配对, 请先上传 mask", started_at)
        return {"status": "FAILURE", "reason": "empty_dataset"}

    # ---- v2.5.27 修复: 数据集统计 sticky_meta (与 detection_tasks / tasks.py 一致) ----
    # 之前: 分割 _train_cb 只推 info, 不推 data_total/data_train/data_val/num_classes/
    #       class_names, SSE 详情页「总样本数/训练集/验证集/类别数」四联全显 0
    # 现在: 训练启动那一刻一次性推送 + 调 _persist_dataset_stats 写库,
    #       后续任意时刻查 /jobs/{id} 都能恢复完整统计
    async def _load_categories():
        async with AsyncSessionLocal() as db:
            from app.models.category import Category
            rows = (await db.execute(
                select(Category).where(Category.dataset_id == dataset_id)
            )).scalars().all()
            return [c.name for c in rows]
    category_names = _run_async(_load_categories())
    n_total = len(masks)
    # 与 detection / classification 保持一致: 80/20 划分 (仅用于展示, 训练仍用全集)
    n_train = int(n_total * 0.8)
    n_val = n_total - n_train
    sticky_meta: dict = {
        "data_total": n_total,
        "data_train": n_train,
        "data_val": n_val,
        "num_classes": None,  # 训练开始后才能确定 (n_cat + 1, 至少 2)
        "class_names": sorted(category_names),
    }

    # 3) 调进度回调
    # v2.5.27 修复: 累积 history_buffer 用于前端曲线 (写 Redis + DB)
    history_buffer: list = []

    def _train_cb(stage, current, total, info, metrics=None):
        # v2.5.27 修复: 进度 0-100 跟 detection/classification 一致 (前端 detailProgress 期望 0-100)
        progress_pct = (current / total * 100.0) if total else 0.0
        meta = {
            "stage": stage,
            "progress": round(progress_pct, 2),
            "current": current,
            "total": total,
            # v2.5.27 修复: SSE 端点训练详情读 current_epoch/total_epochs (不是 current/total)
            "current_epoch": current,
            "total_epochs": total,
            "msg": f"{stage} {current}/{total} {info}".strip(),
            "info": info,  # 兼容老 SSE 端点 / 旧前端
        }
        # v2.5.27 修复: 把结构化指标 (loss/mIoU/pixel_acc/dice) 推到 SSE meta
        # 前端虽然主要从 /training/history/{id} 拿曲线, 但这些字段也方便调试
        if metrics and isinstance(metrics, dict):
            for _k in ("train_loss", "val_loss", "miou", "pixel_acc", "dice"):
                if _k in metrics and isinstance(metrics[_k], (int, float)):
                    meta[_k] = metrics[_k]
            history_buffer.append(metrics)
        if sticky_meta:
            meta.update(sticky_meta)
        _set_task_state(self, "PROGRESS", meta)

        # v2.5.27 修复: 写 Redis history (供前端 5s 轮询实时拿曲线)
        if history_buffer:
            try:
                from app.workers.tasks import _update_training_history
                _update_training_history(self.request.id, list(history_buffer))
            except Exception as e:
                # Redis 不可达不阻塞训练, 走 DB 兜底
                print(f"[warn] seg history -> redis failed: {type(e).__name__}: {e}")

        async def _update_job():
            async with AsyncSessionLocal() as db:
                j = await db.get(TrainingJob, job_id)
                if not j:
                    return
                # 进度也用 0-100
                j.progress = progress_pct
                j.message = f"{stage} {current}/{total} {info}"
                j.current_epoch = current
                # 同步 history 写库 (Redis 失效时的兜底, /history 端点会优先 Redis)
                if history_buffer:
                    j.history = list(history_buffer)
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

        # ---- v2.5.27 修复: num_classes 确定后立即补全 sticky_meta + 推送 + 写库 ----
        # 之前: sticky_meta["num_classes"] = None, SSE 详情页始终 0
        # 现在: 训练真正开始前一次性推完整版 sticky_meta, 写库持久化
        sticky_meta["num_classes"] = num_classes
        _set_task_state(self, "PROGRESS", {
            "progress": 0.0,
            "msg": f"数据集就绪: train={n_train} val={n_val} num_classes={num_classes}",
            "total_epochs": epochs,
            **sticky_meta,
        })
        # 写库 (与 tasks.py 同一辅助函数, 复用)
        try:
            from app.workers.tasks import _persist_dataset_stats
            _persist_dataset_stats(self.request.id, sticky_meta)
        except Exception as e:
            # 写库失败不影响训练
            print(f"[warn] seg _persist_dataset_stats failed: {type(e).__name__}: {e}")

        result = train_segmentation(
            images=images, masks=masks,
            backbone=backbone, num_classes=num_classes,
            epochs=epochs, batch_size=batch_size,
            learning_rate=learning_rate, crop_size=crop_size,
            device=device, progress_cb=_train_cb,
        )
    except Exception as e:
        # v2.5.28 修复: 失败也写 DB (finished_at + duration_seconds + error)
        _finish_failed_job(self, job_id, f"训练失败: {e}", started_at)
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
            j.progress = 100.0  # v2.5.27: 跟训练中保持 0-100 一致
            j.message = f"mIoU={result['best_miou']:.4f}"
            j.model_version_id = mv.id
            # v2.5.28: 写 finished_at + duration_seconds (用 server-now 不用 train 内部 duration,
            # 因为后者不含 worker 启动 + 模型落盘开销)
            finished_at = datetime.utcnow()
            j.finished_at = finished_at
            j.duration_seconds = (finished_at - started_at).total_seconds()
            # v2.5.27: 终态持久化 history (Redis 失效兜底)
            if history_buffer:
                j.history = list(history_buffer)
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
