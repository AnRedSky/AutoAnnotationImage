"""
Celery Tasks: 目标检测训练 + 自动标注 (v3.0.0 Phase 5 薄化)
======================================================

- train_detection_task:        训练 YOLOv8 (委托 TrainingLifecycleService)
- auto_annotate_detection_task: 用已训练模型批量预测
- auto_annotate_pretrained_task: 用预训练 YOLOv8 批量预测

**v3.0.0 Phase 5 重构**:
- 业务编排 (TrainingJob 状态机 / sticky_meta / 历史推送 / 失败清理) 全部下沉到
  TrainingLifecycleService, worker 主体从 ~700 行减到 ~350 行
- ML 模块 (yolo_train/yolo_dataset/yolo_predict) 保持纯计算
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.workers.celery_app import celery_app
from app.core.celery_utils import run_async_in_worker as _run_async

# 早期: 与 tasks.py 同样的 HF symlink + 缓存目录兜底
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
def train_detection_task(
    self,
    dataset_id: int,
    user_id: int,
    model_name: str = "yolov8n",
    model_alias: str = "yolov8n_run",
    epochs: int = 10,
    imgsz: int = 320,
    batch: int = 8,
    val_ratio: float = 0.2,
    device: str = "cpu",
):
    """异步 YOLOv8 训练 (Phase 5: 编排下沉到 TrainingLifecycleService)"""
    from app.ml.detection import export_yolo_dataset, train_yolo, YoloTrainError
    from app.services import TrainingLifecycleService

    task_id = self.request.id
    started_at = datetime.utcnow()

    # ---- 1) 创建/复用 TrainingJob (委托 Service) ----
    job_id = TrainingLifecycleService.create_or_reset_job_sync(
        task_id=task_id,
        user_id=user_id,
        dataset_id=dataset_id,
        base_model=model_name,
        model_name=model_alias,
        task_type="detection",
        epochs=epochs,
        batch_size=batch,
        learning_rate=0.0,
        started_at=started_at,
    )

    # ---- 2) 共享状态 ----
    sticky_meta: dict = {}
    history_buffer: list = []
    workdir = settings.DATA_DIR / "yolo" / f"{model_alias}_{task_id}"

    def _export_cb(stage, current, total, info=""):
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": round(current / max(total, 1) * 100, 2),
            "msg": f"[{stage}] {info}",
            "total_epochs": epochs,
            **sticky_meta,
        })

    def _train_cb(stage, current_epoch, total_epochs, metrics):
        history_buffer.append({
            "epoch": current_epoch,
            "total_epochs": total_epochs,
            **metrics,
        })
        progress_pct = round(current_epoch / max(total_epochs, 1) * 100, 2)
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": progress_pct,
            "msg": f"训练 epoch {current_epoch}/{total_epochs}",
            "total_epochs": total_epochs,
            "current_epoch": current_epoch,
            **{f"train_{k}": v for k, v in metrics.items()
               if isinstance(v, (int, float))},
            **sticky_meta,
        })
        # 推历史曲线 (Redis + DB)
        TrainingLifecycleService.push_history(
            task_id, list(history_buffer),
            job_id=job_id,
            progress=progress_pct,
            message=f"训练 epoch {current_epoch}/{total_epochs}",
            current_epoch=current_epoch,
        )

    # ---- 3) 导 YOLO 数据集 ----
    try:
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": 1.0,
            "msg": "正在导出 YOLO 数据集...",
            "total_epochs": epochs,
        })

        async def _export():
            from app.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                return await export_yolo_dataset(
                    db=db, dataset_id=dataset_id,
                    workdir=workdir, val_ratio=val_ratio,
                    progress_cb=_export_cb,
                )

        export_info = _run_async(_export())
        sticky_meta["data_total"] = export_info["train_count"] + export_info["val_count"]
        sticky_meta["data_train"] = export_info["train_count"]
        sticky_meta["data_val"] = export_info["val_count"]
        sticky_meta["num_classes"] = len(export_info["classes"])
        sticky_meta["class_names"] = export_info["classes"]
        # 跨函数透传 (失败路径也能拿到)
        TrainingLifecycleService.set_last_sticky_meta(task_id, sticky_meta)

        # 数据集统计写库 + 推送
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            **sticky_meta,
            "progress": 5.0,
            "msg": f"数据集就绪: train={export_info['train_count']} val={export_info['val_count']}",
            "total_epochs": epochs,
        })
        TrainingLifecycleService.persist_dataset_stats_sync(task_id, sticky_meta)

        # ---- 4) 跑训练 (纯 ML) ----
        result = train_yolo(
            data_yaml=export_info["data_yaml"],
            model_name=f"{model_name}.pt",
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            project=str(settings.MODEL_DIR / "runs"),
            name=model_alias,
            progress_cb=_train_cb,
        )

        # ---- 5) 写 ModelVersion + TrainingJob SUCCESS (委托 Service) ----
        mv_id = TrainingLifecycleService.create_model_version_sync(
            name=model_alias,
            base_model=model_name,
            dataset_id=dataset_id,
            task_type="detection",
            num_classes=len(export_info["classes"]),
            file_path=result["best_pt"],
            metrics=result["metrics"],
            history=history_buffer,
        )
        TrainingLifecycleService.mark_success_sync(
            job_id=job_id,
            started_at=started_at,
            history_buffer=history_buffer,
            message=f"训练完成 mAP50={result['metrics'].get('map_50', 0):.4f}",
            model_version_id=mv_id,
            sticky_meta=sticky_meta,
        )
        return {
            "status": "SUCCESS", "job_id": job_id, "model_version_id": mv_id,
            "best_pt": result["best_pt"], "metrics": result["metrics"],
        }

    except YoloTrainError as e:
        _finish_failed(self, job_id, e, started_at, task_id)
        return {"status": "FAILURE", "job_id": job_id, "error": str(e)[:500]}
    except Exception as e:
        _finish_failed(self, job_id, e, started_at, task_id)
        return {"status": "FAILURE", "job_id": job_id, "error": str(e)[:500]}
    finally:
        # 清理临时数据集导出目录
        shutil.rmtree(workdir, ignore_errors=True)


def _finish_failed(self, job_id: int, exc: Exception, started_at: datetime, task_id: str):
    """训练失败统一清理 (Phase 5: 委托 TrainingLifecycleService)"""
    from app.services import TrainingLifecycleService

    sticky_meta = TrainingLifecycleService.get_last_sticky_meta(task_id)
    TrainingLifecycleService.mark_failure_sync(
        job_id=job_id,
        error=str(exc),
        started_at=started_at,
        exc_type=type(exc).__name__,
        sticky_meta=sticky_meta,
    )
    TrainingLifecycleService.set_task_state(self, "FAILURE", {
        "exc_type": type(exc).__name__,
        "exc_message": str(exc)[:200],
        "error": str(exc)[:500],
        "job_id": job_id,
    })


# ============== 自动标注 (用已训练模型) ==============

@celery_app.task(bind=True)
def auto_annotate_detection_task(
    self,
    dataset_id: int,
    user_id: int,
    model_version_id: int,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    imgsz: int = 320,
    device: str = "cpu",
    overwrite_existing: bool = False,
):
    """用已训练好的 YOLOv8 模型批量预标注 (Phase 5: 编排下沉)"""
    from app.ml.detection import predict_image_grouped, YoloTrainError
    from app.services import TrainingLifecycleService

    task_id = self.request.id

    try:
        # ---- 1) 加载 ModelVersion ----
        async def _load_mv():
            from app.database import AsyncSessionLocal
            from app.model.model_version import ModelVersion
            async with AsyncSessionLocal() as db:
                return await db.get(ModelVersion, model_version_id)
        mv = _run_async(_load_mv())
        if not mv or not mv.file_path or not Path(mv.file_path).exists():
            raise YoloTrainError(f"ModelVersion id={model_version_id} 权重不存在")

        # ---- 2) 加载图片 / 类目 / 绝对路径 (worker 内联, 数据访问) ----
        async def _load_data():
            from sqlalchemy import select
            from app.database import AsyncSessionLocal
            from app.model.image import Image as ImageModel
            from app.model.category import Category
            from app.services.storage_service import storage_service
            async with AsyncSessionLocal() as db:
                imgs = (await db.execute(
                    select(ImageModel).where(
                        ImageModel.dataset_id == dataset_id,
                        ImageModel.task_type == "detection",
                    ).order_by(ImageModel.id.asc())
                )).scalars().all()
                cats = (await db.execute(
                    select(Category).where(Category.dataset_id == dataset_id).order_by(Category.id.asc())
                )).scalars().all()
                base = Path(storage_service.base_dir).resolve()
                abs_paths, valid_ids = [], []
                for r in imgs:
                    p = Path(r.storage_path)
                    if not p.is_absolute():
                        p = (base / r.storage_path).resolve()
                    if p.exists():
                        abs_paths.append(str(p))
                        valid_ids.append(r.id)
                return list(cats), abs_paths, valid_ids
        cats, abs_paths, valid_ids = _run_async(_load_data())
        if not valid_ids:
            return {"status": "SUCCESS", "total": 0, "auto_labeled": 0, "no_match": 0}

        # ---- 3) 推理 (纯 ML) ----
        def _progress_cb(p, msg):
            TrainingLifecycleService.set_task_state(self, "PROGRESS", {
                "progress": round(p, 2),
                "msg": msg,
                "total": len(abs_paths),
            })

        grouped = predict_image_grouped(
            weights_path=mv.file_path,
            image_paths=abs_paths,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
            imgsz=imgsz,
            device=device,
        )

        # ---- 4) 写 BBoxAnnotation (worker 内联, 数据访问) ----
        from app.model.bbox_annotation import BBoxAnnotation
        from app.model.annotation_log import AnnotationLog
        from sqlalchemy import select, delete as sa_delete
        from app.database import AsyncSessionLocal

        async def _write_results():
            async with AsyncSessionLocal() as db:
                index_to_cat = {i: c for i, c in enumerate(cats)}
                auto_labeled = 0
                no_match = 0
                for i, (img_id, p) in enumerate(zip(valid_ids, abs_paths)):
                    boxes = grouped.get(p, [])
                    if not boxes:
                        no_match += 1
                        continue
                    if overwrite_existing:
                        old = (await db.execute(
                            select(BBoxAnnotation).where(BBoxAnnotation.image_id == img_id)
                        )).scalars().all()
                        for o in old:
                            await db.delete(o)
                    for bb in boxes:
                        cat = index_to_cat.get(bb.class_index)
                        if cat is None:
                            continue
                        db.add(BBoxAnnotation(
                            image_id=img_id,
                            category_id=cat.id,
                            x_min=bb.x_min, y_min=bb.y_min,
                            x_max=bb.x_max, y_max=bb.y_max,
                            confidence=bb.confidence,
                            source="ai",
                            annotated_by=user_id,
                        ))
                        db.add(AnnotationLog(
                            image_id=img_id, user_id=user_id,
                            action="ai_predict", time_spent_ms=0,
                        ))
                    auto_labeled += 1
                    if (i + 1) % 5 == 0 or i == len(valid_ids) - 1:
                        await db.commit()
                        _progress_cb(
                            (i + 1) / max(len(valid_ids), 1) * 100,
                            f"已标注 {i+1}/{len(valid_ids)}",
                        )
                return auto_labeled, no_match
        auto_labeled, no_match = _run_async(_write_results())
        return {
            "status": "SUCCESS",
            "total": len(valid_ids),
            "auto_labeled": auto_labeled,
            "no_match": no_match,
        }

    except Exception as e:
        TrainingLifecycleService.set_task_state(self, "FAILURE", {
            "exc_type": type(e).__name__,
            "exc_message": str(e)[:200],
            "error": str(e)[:500],
        })
        return {"status": "FAILURE", "error": str(e)[:500]}


# ============== 自动标注 (用预训练模型) ==============

PREDEFINED_YOLO_MODELS = {"yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x"}


@celery_app.task(bind=True, name="detection.auto_annotate_pretrained")
def auto_annotate_pretrained_task(
    self,
    dataset_id: int,
    user_id: int,
    model_name: str = "yolov8n",
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    imgsz: int = 320,
    device: str = "cpu",
    overwrite_existing: bool = False,
):
    """用 ultralytics 预训练 YOLOv8n/s/m/l/x 做 detection 数据集预标注 (Phase 5: 编排下沉)"""
    from app.ml.detection import predict_image_grouped, YoloTrainError
    from app.services import TrainingLifecycleService

    task_id = self.request.id

    if model_name not in PREDEFINED_YOLO_MODELS:
        return {
            "status": "FAILURE",
            "error": f"不支持的预训练模型: {model_name}, 可选 {PREDEFINED_YOLO_MODELS}",
        }

    try:
        # ---- 1) 加载图片 / 类目 (worker 内联) ----
        async def _load_data():
            from sqlalchemy import select
            from app.database import AsyncSessionLocal
            from app.model.image import Image as ImageModel
            from app.model.category import Category
            from app.services.storage_service import storage_service
            async with AsyncSessionLocal() as db:
                imgs = (await db.execute(
                    select(ImageModel).where(
                        ImageModel.dataset_id == dataset_id,
                        ImageModel.task_type == "detection",
                    ).order_by(ImageModel.id.asc())
                )).scalars().all()
                cats = (await db.execute(
                    select(Category).where(Category.dataset_id == dataset_id).order_by(Category.id.asc())
                )).scalars().all()
                base = Path(storage_service.base_dir).resolve()
                abs_paths, valid_ids = [], []
                for r in imgs:
                    p = Path(r.storage_path)
                    if not p.is_absolute():
                        p = (base / r.storage_path).resolve()
                    if p.exists():
                        abs_paths.append(str(p))
                        valid_ids.append(r.id)
                return list(cats), abs_paths, valid_ids
        cats, abs_paths, valid_ids = _run_async(_load_data())
        if not valid_ids or not abs_paths:
            return {"status": "SUCCESS", "total": 0, "auto_labeled": 0, "no_match": 0}

        # ---- 2) 加载预训练权重 ----
        try:
            from ultralytics import YOLO
        except ImportError as e:
            return {"status": "FAILURE", "error": f"ultralytics 未安装: {e}"}
        try:
            pretrained = YOLO(f"{model_name}.pt")
            weights_path = str(pretrained.ckpt_path) if hasattr(pretrained, "ckpt_path") else f"{model_name}.pt"
        except Exception as e:
            return {"status": "FAILURE", "error": f"加载预训练权重失败: {e}"}

        # ---- 3) 推理 ----
        def _progress_cb(p, msg):
            TrainingLifecycleService.set_task_state(self, "PROGRESS", {
                "progress": round(p, 2),
                "msg": msg,
                "total": len(abs_paths),
            })

        grouped = predict_image_grouped(
            weights_path=weights_path,
            image_paths=abs_paths,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
            imgsz=imgsz,
            device=device,
        )

        # ---- 4) 写 BBoxAnnotation (pretrained 模式带 model_name 审计) ----
        from app.model.bbox_annotation import BBoxAnnotation
        from app.model.annotation_log import AnnotationLog
        from sqlalchemy import delete as sa_delete
        from app.database import AsyncSessionLocal

        async def _write_results():
            async with AsyncSessionLocal() as db:
                index_to_cat = {i: c for i, c in enumerate(cats)}
                auto_labeled = 0
                no_match = 0
                for i, (img_id, p) in enumerate(zip(valid_ids, abs_paths)):
                    boxes = grouped.get(p, [])
                    if not boxes:
                        no_match += 1
                        continue
                    if overwrite_existing:
                        await db.execute(
                            sa_delete(BBoxAnnotation).where(BBoxAnnotation.image_id == img_id)
                        )
                    for b in boxes:
                        cat = index_to_cat.get(b.get("class_index"))
                        if not cat:
                            continue
                        db.add(BBoxAnnotation(
                            image_id=img_id,
                            category_id=cat.id,
                            x_min=b["x_min"], y_min=b["y_min"],
                            x_max=b["x_max"], y_max=b["y_max"],
                            confidence=float(b.get("confidence", 0.0)),
                            source="ai",
                        ))
                    db.add(AnnotationLog(
                        image_id=img_id, user_id=user_id,
                        action="auto_annotate_pretrained",
                        payload={"model": model_name, "n_boxes": len(boxes)},
                    ))
                    auto_labeled += 1
                await db.commit()
                return auto_labeled, no_match

        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": 0.0, "msg": f"写入 bbox (模型={model_name})...",
            "total": len(abs_paths),
        })
        auto_labeled, no_match = _run_async(_write_results())

        TrainingLifecycleService.set_task_state(self, "SUCCESS", {
            "progress": 1.0, "msg": "完成",
            "total": len(abs_paths),
            "auto_labeled": auto_labeled,
            "no_match": no_match,
        })
        return {
            "status": "SUCCESS",
            "model_name": model_name,
            "weights": weights_path,
            "total": len(abs_paths),
            "auto_labeled": auto_labeled,
            "no_match": no_match,
        }

    except Exception as e:
        TrainingLifecycleService.set_task_state(self, "FAILURE", {
            "exc_type": type(e).__name__,
            "exc_message": str(e)[:200],
            "error": str(e)[:500],
        })
        return {"status": "FAILURE", "error": str(e)[:500]}
