"""
image.preview.detection 模块 — 检测置信度预览
============================================

**v3.0.0 Phase S3 拆分**: 从 image/preview.py 抽离
**职责**: 检测任务 (YOLOv8 + fine-tune) 的置信度预览实现

**主要逻辑**:
- 解析 fine-tune ModelVersion (显式 id 优先 → 激活模型 → None)
- 加载模型 (fine-tune 优先, 缺则走 yolov8n 预训练)
- predict_image_grouped 批量推理 → bbox 列表
- 还原类别名 (fine-tune: sorted_cat_names[class_index]; pretrained: COCO names)
- would_label: 至少 1 个 bbox 类目在项目内 + max_conf ≥ 阈值

**S3 严格模式约定**:
- use_finetune=False 时, 若项目有任何 fine-tune 模型, 强制要求 use_finetune=True
"""
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.image import Image
from app.tasks.model.category import Category
from app.tasks.model.dataset import Dataset
from app.tasks.model.model_version import ModelVersion
from app.common.storage import resolve_inference_paths  # v3.4.1 P1: 适配 minio 后端

# 复用 preview 包内的工具函数
from app.tasks.api.preview._utils import _resolve_finetune_model, _attach_model_meta


async def preview_detection(
    db: AsyncSession, dataset: Dataset, images: list,
    *,
    model_name: str, model_id: Optional[int],
    confidence_threshold: float, use_finetune: bool,
) -> dict:
    """检测任务: bbox 预览, 按"是否含 ≥ 阈值 bbox 且在项目类目"判定 would_label"""
    dataset_id = dataset.id
    used_finetune = False
    mv: Optional[ModelVersion] = None
    weights_path: Optional[str] = None

    if use_finetune:
        mv = await _resolve_finetune_model(db, dataset_id, model_id)
        if mv:
            if not Path(mv.file_path).exists():
                raise HTTPException(400, f"Model file missing on disk: {mv.file_path}. Please retrain.")
            weights_path = mv.file_path
            used_finetune = True
        else:
            # 冷启动: 无 fine-tune, 走预训练 yolov8n
            used_finetune = False
            try:
                from ultralytics import YOLO  # lazy
                weights_path = f"{model_name}.pt"  # yolov8n/s/m/l/x
                _ = YOLO(weights_path).names  # 仅首次会下载, 后续命中本地缓存
            except Exception as e:
                raise HTTPException(503, f"Failed to load pretrained {model_name}: {e}")
    else:
        # 严格模式: 有 fine-tune 时强制走 fine-tune
        mv_any = (await db.execute(
            select(ModelVersion.id).order_by(ModelVersion.id.desc()).limit(1)
        )).scalars().first()
        if mv_any is not None:
            raise HTTPException(
                400, "Project has fine-tune models. Preview must use fine-tune models. "
                     "Set use_finetune=True to use the active fine-tune model."
            )
        try:
            from ultralytics import YOLO  # lazy
            weights_path = f"{model_name}.pt"
            _ = YOLO(weights_path).names
        except Exception as e:
            raise HTTPException(503, f"Failed to load pretrained {model_name}: {e}")
        used_finetune = False

    # 取项目类目 (用于"是否在类目内"判定 + fine-tune 时类索引→name 映射)
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    cat_names = [c.name for c in cat_rows]
    cat_name_set = {n.lower() for n in cat_names}
    # fine-tune 时, class_index 是项目类目按 sort 后的索引
    sorted_cat_names = sorted(cat_names)

    # 推理
    from app.tasks.ml.detection.yolo_predict import predict_image_grouped
    # v3.4.1 P1: 推理路径解析 (local 直返 / minio 临时文件)
    # 替代旧写法 [str(storage_root / img.storage_path) for img in images]
    async with resolve_inference_paths(images) as image_paths:
        try:
            grouped = predict_image_grouped(
                weights_path=weights_path,
                image_paths=image_paths,
                conf_threshold=confidence_threshold,
                iou_threshold=0.45, imgsz=640, device="cpu",
            )
        except Exception as e:
            raise HTTPException(500, f"Detection inference failed: {str(e)[:200]}")

        # v3.5.0 Phase T7 #2 优化: 预训练 names 字典提到循环外
        # 原代码在 for b in boxes: 循环内反复 YOLO(weights_path).names
        # 每张图每个 bbox 都重新实例化 YOLO (mmap + yaml + settings 重读)
        # 100 张 × 20 bbox = 2000 次冗余加载, 预览从 < 1s 膨胀到数秒
        # 修复: 进入循环前只取一次, 循环内复用
        pretrained_names: dict = {}
        if not used_finetune:
            from ultralytics import YOLO
            try:
                pretrained_names = YOLO(weights_path).names
            except Exception:
                pretrained_names = {}

        items: list = []
        would_label = 0
        need_human = 0
        no_match = 0
        for img, abs_path in zip(images, image_paths):
            boxes = grouped.get(abs_path, [])
            if not boxes:
                items.append({
                    "image_id": img.id, "filename": img.filename,
                    "thumb_url": f"/api/files/{img.id}/preview",
                    "bbox_count": 0, "max_conf": 0.0, "bboxes": [],
                    "would_label": False, "reason": "no_bbox",
                })
                need_human += 1
                continue
            # 还原类别名 (fine-tune: sorted_cat_names[class_index]; pretrained: COCO names)
            bbox_dicts = []
            max_conf = 0.0
            in_categories_count = 0
            for b in boxes:
                if used_finetune:
                    if 0 <= b.class_index < len(sorted_cat_names):
                        cls_name = sorted_cat_names[b.class_index]
                    else:
                        cls_name = f"class_{b.class_index}"
                else:
                    # v3.5.0 Phase T7 #2: 复用预训练 names 字典 (循环外已取 1 次)
                    # YOLO.names 是 dict[int, str], class_index 是 COCO 索引
                    cls_name = pretrained_names.get(b.class_index, f"class_{b.class_index}")
                in_proj = cls_name.lower() in cat_name_set
                if in_proj:
                    in_categories_count += 1
                max_conf = max(max_conf, b.confidence)
                bbox_dicts.append({
                    "class_index": b.class_index,
                    "class_name": cls_name,
                    "x_min": round(b.x_min, 4), "y_min": round(b.y_min, 4),
                    "x_max": round(b.x_max, 4), "y_max": round(b.y_max, 4),
                    "confidence": round(b.confidence, 4),
                    "in_project_categories": in_proj,
                })
            # would_label: 含 ≥ 阈值 bbox 且至少一个 bbox 类目在项目内
            would = in_categories_count > 0 and max_conf >= confidence_threshold
            reason = "would_label" if would else (
                "below_threshold" if max_conf < confidence_threshold else "not_in_categories"
            )
            items.append({
                "image_id": img.id, "filename": img.filename,
                "thumb_url": f"/api/files/{img.id}/preview",
                "bbox_count": len(bbox_dicts),
                "max_conf": round(max_conf, 4),
                "in_categories_count": in_categories_count,
                "bboxes": bbox_dicts,
                "would_label": would, "reason": reason,
            })
            if would:
                would_label += 1
            else:
                need_human += 1

    payload = {
        "items": items,
        "would_label": would_label, "need_human": need_human, "no_match": no_match,
        "total": len(items), "threshold": confidence_threshold,
        "model_name": model_name,
        "task_type": "detection",
    }
    payload = _attach_model_meta(
        payload, used_finetune=used_finetune, mv=mv,
        fallback_pretrained_name=model_name,
        use_finetune=use_finetune,
    )
    return payload
