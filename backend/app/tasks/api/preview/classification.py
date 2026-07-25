"""
image.preview.classification 模块 — 分类置信度预览
================================================

**v3.0.0 Phase S3 拆分**: 从 image/preview.py 抽离
**职责**: 分类任务 (timm + fine-tune) 的置信度预览实现

**主要逻辑**:
- 解析 fine-tune ModelVersion (显式 id 优先 → 激活模型 → None)
- 加载模型 (fine-tune 优先, 缺则走 ImageNet 预训练)
- 批量推理 + filter_predictions_to_categories (限定到项目类目)
- 计算 top-1 置信度 + would_label (置信度 ≥ 阈值 + 在项目类目内)

**S3 严格模式约定**:
- use_finetune=False 时, 若项目有任何 fine-tune 模型, 强制要求 use_finetune=True
- 防止误用 ImageNet 预训练结果作为最终标注
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
from app.common.ml.ai_service import ai_service, filter_predictions_to_categories
from app.core.config import settings

# 复用 preview 包内的工具函数
from app.tasks.api.preview._utils import _resolve_finetune_model, _attach_model_meta


async def preview_classification(
    db: AsyncSession, dataset: Dataset, images: list,
    *,
    model_name: str, model_id: Optional[int],
    confidence_threshold: float, use_finetune: bool,
) -> dict:
    """分类任务: 单标签 top-1 预览 (原 preview_confidence 行为)"""
    dataset_id = dataset.id
    used_finetune = False
    mv: Optional[ModelVersion] = None
    if use_finetune:
        mv = await _resolve_finetune_model(db, dataset_id, model_id)
        if mv:
            if not Path(mv.file_path).exists():
                raise HTTPException(400, f"Model file missing on disk: {mv.file_path}. Please retrain.")
            try:
                await ai_service.load_local(mv.base_model, mv.file_path, mv.num_classes)
            except Exception as e:
                raise HTTPException(500, f"Failed to load fine-tune model: {e}")
            cats = (await db.execute(
                select(Category).where(Category.dataset_id == dataset_id)
            )).scalars().all()
            if not cats:
                raise HTTPException(
                    400, "Dataset has no categories. Please add categories first (Dataset -> 类别)."
                )
            sorted_names = sorted({c.name for c in cats})
            ai_service.set_label_map({i: name for i, name in enumerate(sorted_names)})
            used_finetune = True
        else:
            ai_service.set_label_map(None)
            if ai_service.current_model_name != model_name:
                try:
                    await ai_service.load_pretrained(model_name)
                except Exception as e:
                    raise HTTPException(500, f"Failed to load pretrained model: {e}")
            used_finetune = False
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
        ai_service.set_label_map(None)
        if ai_service.current_model_name != model_name:
            try:
                await ai_service.load_pretrained(model_name)
            except Exception as e:
                raise HTTPException(500, f"Failed to load model: {e}")
        used_finetune = False

    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    predictions = await ai_service.batch_predict(image_paths, top_k=5)

    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    category_names = [c.name for c in cat_rows]
    if used_finetune:
        final_predictions = predictions
    else:
        final_predictions = filter_predictions_to_categories(predictions, category_names)

    items: list = []
    would_label = 0
    need_human = 0
    no_match = 0
    for img, pred in zip(images, final_predictions):
        if pred is None:
            items.append({
                "image_id": img.id, "filename": img.filename,
                "thumb_url": f"/api/files/{img.id}/preview",
                "top1": None, "top1_conf": None, "candidates": [],
                "in_project_categories": False, "would_label": False,
                "reason": "no_match",
            })
            no_match += 1
            continue
        top1 = pred.get("top1")
        top1_conf = float(pred.get("top1_conf") or 0.0)
        in_proj = (top1 in category_names) if top1 else False
        would = (top1_conf >= confidence_threshold) and in_proj
        items.append({
            "image_id": img.id, "filename": img.filename,
            "thumb_url": f"/api/files/{img.id}/preview",
            "top1": top1, "top1_conf": round(top1_conf, 4),
            "candidates": [
                {"label": c.get("label"), "conf": round(float(c.get("conf") or 0.0), 4)}
                for c in (pred.get("candidates") or [])
            ],
            "in_project_categories": in_proj, "would_label": would,
            "reason": "would_label" if would else (
                "below_threshold" if not (top1_conf >= confidence_threshold) else "not_in_categories"
            ),
        })
        if would:
            would_label += 1
        else:
            need_human += 1

    payload = {
        "items": items,
        "would_label": would_label, "need_human": need_human, "no_match": no_match,
        "total": len(items), "threshold": confidence_threshold,
        "model_name": ai_service.current_model_name,
        "task_type": "classification",
    }
    payload = _attach_model_meta(
        payload, used_finetune=used_finetune, mv=mv,
        fallback_pretrained_name=ai_service.current_model_name or model_name,
    )
    return payload
