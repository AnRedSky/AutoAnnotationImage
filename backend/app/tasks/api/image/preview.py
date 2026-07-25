"""
image.preview 模块 — 置信度预览相关 API
=======================================

v3.0.0 Phase N 拆分: 从 image.py 抽离
- 职责: 非破坏性测评接口 (不写库, 不写审计, 仅返回预测结果)
- 三路分派: classification / detection / segmentation
- 私有函数: _resolve_finetune_model / _attach_model_meta / 三个 _preview_*
"""
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.tasks.model.model_version import ModelVersion
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.common.ml.ai_service import ai_service
from app.common.ml.ai_service import filter_predictions_to_categories
from app.core.config import settings

# preview 独立 router (prefix 需在 image/__init__.py 装配时统一加)
router = APIRouter()


# ---------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------
class PreviewConfidenceRequest(BaseModel):
    """
    POST /api/images/preview-confidence 请求体
    非破坏性测评: 对指定图片跑模型, 返回 top-1 置信度及在当前阈值下是否会被自动标注
    """
    model_config = ConfigDict(protected_namespaces=())  # 允许 model_name/model_id 字段
    dataset_id: int = Field(..., description="数据集 id")
    image_ids: List[int] = Field(..., description="要测评的图片 id 列表")
    model_name: Optional[str] = Field(
        default="efficientnet_b0",
        description="timm 模型名 (仅 use_finetune=False 时使用)"
    )
    model_id: Optional[int] = Field(
        default=None,
        description="指定 fine-tune ModelVersion.id; 缺省=激活的"
    )
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    use_finetune: bool = Field(default=True, description="True=用 fine-tune; False=用 timm ImageNet")


# ---------------------------------------------------------------------
# 公共辅助函数
# ---------------------------------------------------------------------
async def _resolve_finetune_model(
    db: AsyncSession, dataset_id: int, model_id: Optional[int],
) -> Optional[ModelVersion]:
    """解析 fine-tune ModelVersion (显式 id 优先, 否则取数据集激活, 仍无则 None)"""
    mv: Optional[ModelVersion] = None
    if model_id is not None:
        mv = (await db.execute(
            select(ModelVersion).where(ModelVersion.id == model_id)
        )).scalars().first()
    if mv is None:
        mv = (await db.execute(
            select(ModelVersion)
            .where(ModelVersion.is_active == True, ModelVersion.dataset_id == dataset_id)  # noqa: E712
            .order_by(ModelVersion.id.desc()).limit(1)
        )).scalars().first()
    return mv


def _attach_model_meta(
    payload: dict, *,
    used_finetune: bool, mv: Optional[ModelVersion],
    fallback_pretrained_name: str,
) -> dict:
    """把模型元信息统一塞到 payload (前端稳定依赖这些字段)"""
    payload["used_finetune"] = used_finetune
    payload["finetune_name"] = mv.name if (used_finetune and mv is not None) else None
    payload["base_model"] = (
        mv.base_model if (used_finetune and mv is not None)
        else fallback_pretrained_name
    )
    payload["model_id"] = mv.id if (used_finetune and mv is not None) else None
    payload["fallback_to_pretrained"] = False
    payload["warning"] = None
    return payload


# ---------------------------------------------------------------------
# 路由入口
# ---------------------------------------------------------------------
@router.post("/preview-confidence")
async def preview_confidence(
    req: PreviewConfidenceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    **非破坏性** 测评接口: 对指定图片跑模型, 返回 top-1 置信度及在当前阈值下是否会被自动标注

    v2.5.46 改造: 按 dataset.task_type 三路分派
    - classification: 单标签 top-1 预览 (原行为, 保留)
    - detection:     bbox 列表预览, 按"是否含 ≥ 阈值 bbox"判定 would_label
    - segmentation:  mask 预览, 按 max_softmax 是否 ≥ 阈值判定 would_label

    用途: 在点击「启动 AI 预标注」之前, 让用户先看到「如果现在跑批量预标注, 哪些图会被标、哪些会留在待标注」
    - **完全不改数据库** (不写 ai_prediction, 不改 status)
    - 不写 AnnotationLog 审计
    - 用户可反复调整阈值/模型预览, 选定后再点击批量预标注
    """
    dataset_id = req.dataset_id
    image_ids = req.image_ids
    model_name = req.model_name or "efficientnet_b0"
    model_id = req.model_id
    confidence_threshold = req.confidence_threshold
    use_finetune = req.use_finetune

    if not image_ids:
        return {
            "items": [],
            "would_label": 0,
            "need_human": 0,
            "no_match": 0,
            "threshold": confidence_threshold,
            "model_name": None,
            "used_finetune": False,
        }

    # 1. 取图片 (限定数据集 + 给定 id 集合)
    result = await db.execute(
        select(Image).where(
            Image.dataset_id == dataset_id,
            Image.id.in_(image_ids),
        )
    )
    images = result.scalars().all()
    if not images:
        return {
            "items": [],
            "would_label": 0,
            "need_human": 0,
            "no_match": 0,
            "total": 0,
            "threshold": confidence_threshold,
            "model_name": None,
            "used_finetune": False,
            "finetune_name": None,
            "base_model": None,
            "model_id": None,
            "fallback_to_pretrained": False,
        }

    # v2.5.46: 按 dataset.task_type 三路分派
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    task_type = (dataset.task_type or "classification").lower()

    if task_type == "detection":
        return await _preview_detection(
            db, dataset, images,
            model_name=model_name, model_id=model_id,
            confidence_threshold=confidence_threshold, use_finetune=use_finetune,
        )
    if task_type == "segmentation":
        return await _preview_segmentation(
            db, dataset, images,
            model_name=model_name, model_id=model_id,
            confidence_threshold=confidence_threshold, use_finetune=use_finetune,
        )
    # default: classification (沿用原实现)
    return await _preview_classification(
        db, dataset, images,
        model_name=model_name, model_id=model_id,
        confidence_threshold=confidence_threshold, use_finetune=use_finetune,
    )


# ---------------------------------------------------------------------
# v2.5.46: 测评分派 — 三个 task_type 私有函数
# ---------------------------------------------------------------------
async def _preview_classification(
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


async def _preview_detection(
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
    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    try:
        grouped = predict_image_grouped(
            weights_path=weights_path,
            image_paths=image_paths,
            conf_threshold=confidence_threshold,
            iou_threshold=0.45, imgsz=640, device="cpu",
        )
    except Exception as e:
        raise HTTPException(500, f"Detection inference failed: {str(e)[:200]}")

    items: list = []
    would_label = 0
    need_human = 0
    no_match = 0
    for img in images:
        abs_path = str(storage_root / img.storage_path)
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
                # YOLO.names 是 dict[int, str], class_index 是 COCO 索引
                from ultralytics import YOLO
                try:
                    names = YOLO(weights_path).names
                    cls_name = names.get(b.class_index, f"class_{b.class_index}")
                except Exception:
                    cls_name = f"class_{b.class_index}"
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
    )
    return payload


async def _preview_segmentation(
    db: AsyncSession, dataset: Dataset, images: list,
    *,
    model_name: str, model_id: Optional[int],
    confidence_threshold: float, use_finetune: bool,
) -> dict:
    """分割任务: 走 torchvision / fine-tune, 按 max_softmax 判定 would_label"""
    dataset_id = dataset.id
    used_finetune = False
    mv: Optional[ModelVersion] = None

    from app.tasks.ml.segmentation import seg_predict

    if use_finetune:
        mv = await _resolve_finetune_model(db, dataset_id, model_id)
        if mv:
            if not Path(mv.file_path).exists():
                raise HTTPException(400, f"Model file missing on disk: {mv.file_path}. Please retrain.")
            try:
                # 复用 load_model(state_dict_bytes, ...) 思路: 直接读 weights 字节
                with open(mv.file_path, "rb") as f:
                    sd_bytes = f.read()
                model = seg_predict.load_model(
                    sd_bytes, backbone=mv.base_model or "deeplabv3_resnet50",
                    num_classes=mv.num_classes, device="cpu",
                )
            except Exception as e:
                raise HTTPException(500, f"Failed to load fine-tune segmentation model: {e}")
            used_finetune = True
        else:
            # 冷启动: 走 torchvision 预训练
            try:
                model = seg_predict.load_pretrained_torchvision(model_name, device="cpu")
            except ValueError as ve:
                raise HTTPException(400, str(ve))
            except Exception as e:
                err_msg = str(e)[:200]
                if any(k in err_msg.lower() for k in ["winerror 10060", "connection", "timeout", "huggingface"]):
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            f"Model '{model_name}' cannot be loaded: no internet/HuggingFace access "
                            f"({err_msg}). Please pre-download the weights or set HF_HUB_OFFLINE=1."
                        ),
                    )
                raise HTTPException(500, f"Failed to load pretrained segmentation model: {err_msg}")
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
        try:
            model = seg_predict.load_pretrained_torchvision(model_name, device="cpu")
        except ValueError as ve:
            raise HTTPException(400, str(ve))
        except Exception as e:
            err_msg = str(e)[:200]
            if any(k in err_msg.lower() for k in ["winerror 10060", "connection", "timeout", "huggingface"]):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Model '{model_name}' cannot be loaded: no internet/HuggingFace access "
                        f"({err_msg}). Please pre-download the weights or set HF_HUB_OFFLINE=1."
                    ),
                )
            raise HTTPException(500, f"Failed to load pretrained segmentation model: {err_msg}")
        used_finetune = False

    # 推理
    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    try:
        masks_with_conf = seg_predict.predict_to_mask_image_with_conf(
            model, image_paths, crop_size=256, device="cpu",
        )
    except Exception as e:
        raise HTTPException(500, f"Segmentation inference failed: {str(e)[:200]}")

    items: list = []
    would_label = 0
    need_human = 0
    for img in images:
        abs_path = str(storage_root / img.storage_path)
        if abs_path not in masks_with_conf:
            items.append({
                "image_id": img.id, "filename": img.filename,
                "thumb_url": f"/api/files/{img.id}/preview",
                "max_conf": 0.0, "would_label": False, "reason": "infer_failed",
            })
            need_human += 1
            continue
        _, max_softmax = masks_with_conf[abs_path]
        would = max_softmax >= confidence_threshold
        items.append({
            "image_id": img.id, "filename": img.filename,
            "thumb_url": f"/api/files/{img.id}/preview",
            "max_conf": round(max_softmax, 4),
            "would_label": would,
            "reason": "would_label" if would else "below_threshold",
        })
        if would:
            would_label += 1
        else:
            need_human += 1

    payload = {
        "items": items,
        "would_label": would_label, "need_human": need_human, "no_match": 0,
        "total": len(items), "threshold": confidence_threshold,
        "model_name": model_name,
        "task_type": "segmentation",
    }
    payload = _attach_model_meta(
        payload, used_finetune=used_finetune, mv=mv,
        fallback_pretrained_name=model_name,
    )
    return payload
