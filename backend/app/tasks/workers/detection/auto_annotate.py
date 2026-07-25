"""
workers.detection.auto_annotate 模块 — 自动标注任务
==================================================

**v3.0.0 Phase S4 拆分**: 从 workers/detection.py 抽离
**职责**: 
- auto_annotate_detection_task (用已训练 YOLOv8 模型批量预标注)
- auto_annotate_pretrained_task (用预训练 yolov8n/s/m/l/x 批量预标注)
- PREDEFINED_YOLO_MODELS (预训练模型白名单)

**Celery 字符串路径**:
- `app.tasks.workers.detection:auto_annotate_detection_task`
- `app.tasks.workers.detection:auto_annotate_pretrained_task`

注意: Phase S4 拆分后, 物理位置在 auto_annotate.py, 但 __init__.py 重新导出,
保持字符串路径和外部 import 完全向后兼容.

**流程** (与 train 不同, 不写 TrainingJob, 仅 Celery state):
1. 加载 ModelVersion / 加载预训练权重
2. 加载图片 + 类目 (worker 内联, 数据访问)
3. predict_image_grouped 推理 (纯 ML)
4. 写 BBoxAnnotation + AnnotationLog 审计
5. SUCCESS / FAILURE 状态推送

**pretrained 模式特殊点**:
- 带 model_name 审计 (payload: {model: model_name, n_boxes})
- 不需要 ModelVersion (走预训练)
- class_index 是 COCO 索引 → 通过 YOLO.names 还原类名
"""
from pathlib import Path

from app.tasks.workers.celery_app import celery_app
from app.utils.async_helpers import run_async_in_worker as _run_async


PREDEFINED_YOLO_MODELS = {"yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x"}


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
    from app.tasks.ml.detection import predict_image_grouped, YoloTrainError
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    task_id = self.request.id

    try:
        # ---- 1) 加载 ModelVersion ----
        async def _load_mv():
            from app.database import AsyncSessionLocal
            from app.tasks.model.model_version import ModelVersion
            async with AsyncSessionLocal() as db:
                return await db.get(ModelVersion, model_version_id)
        mv = _run_async(_load_mv())
        if not mv or not mv.file_path or not Path(mv.file_path).exists():
            raise YoloTrainError(f"ModelVersion id={model_version_id} 权重不存在")

        # ---- 2) 加载图片 / 类目 / 绝对路径 (worker 内联, 数据访问) ----
        async def _load_data():
            from sqlalchemy import select
            from app.database import AsyncSessionLocal
            from app.tasks.model.image import Image as ImageModel
            from app.tasks.model.category import Category
            from app.common.storage.storage_service import storage_service
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
        from app.annotation.model.bbox_annotation import BBoxAnnotation
        from app.tasks.model.annotation_log import AnnotationLog
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
    from app.tasks.ml.detection import predict_image_grouped, YoloTrainError
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

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
            from app.tasks.model.image import Image as ImageModel
            from app.tasks.model.category import Category
            from app.common.storage.storage_service import storage_service
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
        from app.annotation.model.bbox_annotation import BBoxAnnotation
        from app.tasks.model.annotation_log import AnnotationLog
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
