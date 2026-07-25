"""
Image API: Upload / Auto-Label / Query / Detail / Delete
=======================================================
核心接口: 图像上传、AI 自动标注、列表查询、单图详情、删除、批量删除
"""
from typing import List, Optional
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func, case
from PIL import Image as PILImage
from pydantic import BaseModel, ConfigDict, Field
from io import BytesIO
from datetime import datetime

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.tasks.model.annotation_log import AnnotationLog
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.common.ml.ai_service import ai_service
from app.common.ml.ai_service import filter_predictions_to_categories
from app.common.storage.storage_service import storage_service
# v3.0.0 Phase 4: 业务编排下沉到 Service
from app.tasks.service.image_service import ImageService
from app.core.config import settings
from app.tasks.model.model_version import ModelVersion

router = APIRouter()


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


@router.post("/upload/{dataset_id}")
async def upload_images(
    dataset_id: int,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量上传图片到指定数据集
    Returns: 上传结果列表 (含 ID、文件名、是否去重)
    """
    # 校验数据集
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    results = []
    for file in files:
        content = await file.read()
        file_hash = storage_service.compute_hash(content)

        # 去重
        existing = await db.execute(
            select(Image).where(
                Image.dataset_id == dataset_id,
                Image.file_hash == file_hash,
            )
        )
        if existing.scalar_one_or_none():
            results.append({"filename": file.filename, "duplicate": True})
            continue

        # 保存文件
        storage_key = storage_service.generate_key(dataset_id, file.filename, file_hash)
        await storage_service.save(storage_key, content)

        # 读取图片尺寸
        try:
            pil_img = PILImage.open(BytesIO(content))
            width, height = pil_img.size
        except Exception:
            width, height = None, None

        # 写库
        # v2.0.0: 冗余 task_type 到 image 表, 避免后续每条都 join dataset
        img = Image(
            dataset_id=dataset_id,
            filename=file.filename,
            storage_path=storage_key,
            file_size=len(content),
            width=width,
            height=height,
            file_hash=file_hash,
            status="pending",
            task_type=dataset.task_type,  # 同步数据集的 task_type
        )
        db.add(img)
        results.append({"filename": file.filename, "duplicate": False})

    # 更新数据集图片数
    await db.execute(
        update(Dataset)
        .where(Dataset.id == dataset_id)
        .values(image_count=Dataset.image_count + len([r for r in results if not r["duplicate"]]))
    )
    await db.commit()

    return {
        "total": len(files),
        "uploaded": len([r for r in results if not r["duplicate"]]),
        "duplicates": len([r for r in results if r["duplicate"]]),
        "items": results,
    }


@router.post("/auto-label/{dataset_id}")
async def auto_label(
    dataset_id: int,
    model_name: str = Query(default="efficientnet_b0", description="timm 模型名 (仅当 use_finetune=False 时使用)"),
    model_id: Optional[int] = Query(default=None, description="指定具体 fine-tune ModelVersion.id; 缺省=激活的; 仅 use_finetune=True 时生效"),
    confidence_threshold: float = Query(default=0.6, ge=0.0, le=1.0),
    use_finetune: bool = Query(default=True, description="True=用项目训练的 fine-tune 模型 (DB 中 is_active); False=用 timm ImageNet 预训练 (输出不在项目类目, 会被前端归一为「未知」)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量自动标注
    - 严格模式: 优先用项目训练的 fine-tune 模型, 预标注结果才能落入项目类目
    - use_finetune=True (默认): 优先用 DB 中 is_active 的 fine-tune 模型; 若**无任何激活 fine-tune**,
                                自动回退到 timm ImageNet 预训练 (冷启动兜底), 输出过滤到项目类目后
                                仍会写入 final_label_id + status=ai_labeled, 训练可发现
    - use_finetune=True + model_id: 显式指定 ModelVersion.id (支持标注工作台切换多个 fine-tune 模型)
    - use_finetune=False: 显式走 timm ImageNet, 输出 (class_532 等) 过滤到项目类目, 无匹配保持 pending
    """
    # 1. 加载模型
    used_finetune = False
    if use_finetune:
        # 取 fine-tune 模型: 优先 model_id 显式指定, 否则取激活
        mv = None
        if model_id is not None:
            mv = (await db.execute(
                select(ModelVersion).where(ModelVersion.id == model_id)
            )).scalars().first()
            if mv is None:
                raise HTTPException(404, f"ModelVersion id={model_id} not found")
            if not mv.is_active:
                # 不强制, 只提示: 显式选非激活版本会覆盖, 不影响功能
                pass
        if mv is None:
            mv = (await db.execute(
                select(ModelVersion).where(ModelVersion.is_active == True)  # noqa: E712
                .order_by(ModelVersion.id.desc()).limit(1)
            )).scalars().first()
        if mv:
            # 走 fine-tune 分支
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
                    400,
                    "Dataset has no categories. Please add categories first (Dataset -> 类别)."
                )
            sorted_names = sorted({c.name for c in cats})
            ai_service.set_label_map({i: name for i, name in enumerate(sorted_names)})
            used_finetune = True
        else:
            # 冷启动兜底: 没有激活的 fine-tune 模型, 自动回退到 timm ImageNet 预训练
            # - 这样用户能依靠"匹配项目类目"的 top-1 拿到种子数据, 进而训练出第一个 fine-tune 模型
            # - 不写 final_label_id 的图仍可显示在 UI 供人工确认
            # - 显式 log 提示用户这是冷启动, 建议尽快激活 fine-tune 模型
            ai_service.set_label_map(None)
            if ai_service.current_model_name != model_name:
                try:
                    await ai_service.load_pretrained(model_name)
                except Exception as e:
                    raise HTTPException(500, f"Failed to load pretrained model: {e}")
            used_finetune = False
    else:
        # 严格模式兜底: 即使用户显式 use_finetune=False, 也只在项目**完全没 fine-tune** 时允许
        # 一旦有 fine-tune, 强制走 fine-tune 分支 (避免用户误用基础模型)
        mv_any = (await db.execute(
            select(ModelVersion.id).order_by(ModelVersion.id.desc()).limit(1)
        )).scalars().first()
        if mv_any is not None:
            raise HTTPException(
                400,
                "Project has fine-tune models. Pre-annotation must use fine-tune models. "
                "Set use_finetune=True to use the active fine-tune model."
            )
        # 真正的冷启动 (项目无任何 fine-tune): 允许用 ImageNet, 但会标 warning 提醒前端归一
        ai_service.set_label_map(None)
        if ai_service.current_model_name != model_name:
            try:
                await ai_service.load_pretrained(model_name)
            except Exception as e:
                raise HTTPException(500, f"Failed to load model: {e}")
        used_finetune = False

    # 2. 取待标注图片
    result = await db.execute(
        select(Image).where(
            Image.dataset_id == dataset_id,
            Image.status == "pending",
        )
    )
    images = result.scalars().all()
    if not images:
        return {
            "total": 0,
            "auto_labeled": 0,
            "need_human": 0,
            "no_match": 0,
            "avg_confidence": 0.0,
            "threshold": confidence_threshold,
            "used_finetune": used_finetune,   # 早 return 也补, 前端能正确判断
            "model_name": ai_service.current_model_name,
            "model_path": ai_service.current_model_path,
            "model_id": mv.id if (used_finetune and mv is not None) else None,
            "fallback_to_pretrained": (use_finetune and not used_finetune),
            "message": "No pending images",
        }

    # 3. 批量推理
    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    predictions = await ai_service.batch_predict(image_paths, top_k=5)

    # 4. 写回数据库
    # 一次性查全部 category, 把 top1 label 映射回 final_label_id
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    name_to_id = {c.name: c.id for c in cat_rows}
    category_names = [c.name for c in cat_rows]

    # 关键: use_finetune=False (或回退到 ImageNet) 时, base model 输出可能是 class_579 / tabby_cat
    # 等与项目类目无关的标签. 必须过滤到只含项目类目, 无匹配的图保持 pending 不标注
    # use_finetune=True (成功加载 fine-tune) 时输出已在项目类目空间 (label map), 过滤是 no-op
    if used_finetune:
        final_predictions = predictions
    else:
        final_predictions = filter_predictions_to_categories(predictions, category_names)

    auto_labeled = 0
    no_match = 0
    confs_for_avg: list = []
    for img, pred in zip(images, final_predictions):
        if pred is None:
            # 与项目类目无交集: 不写 ai_prediction, 保持 pending
            img.ai_prediction = None
            no_match += 1
            continue
        img.ai_prediction = pred
        confs_for_avg.append(pred["top1_conf"])
        if pred["top1_conf"] >= confidence_threshold:
            img.status = "ai_labeled"
            # 关键: 把 top1 标签落到 final_label_id, 让训练能 JOIN 到 Category
            top1_label = pred.get("top1")
            if top1_label and top1_label in name_to_id:
                img.final_label_id = name_to_id[top1_label]
            # 写 ai_predict 审计 (与人工 confirm/correct/reject 一并出现在历史流)
            db.add(AnnotationLog(
                image_id=img.id,
                user_id=current_user.id,
                action="ai_predict",
                from_label_id=None,  # 该入口的 AI 阶段没有 final_label
                to_label_id=img.final_label_id,
                time_spent_ms=0,
            ))
            auto_labeled += 1
        # 否则保持 pending, 由人工标注

    await db.commit()

    return {
        "total": len(images),
        "auto_labeled": auto_labeled,
        "need_human": len(images) - auto_labeled,
        "no_match": no_match,
        "avg_confidence": round(
            (sum(confs_for_avg) / len(confs_for_avg)) if confs_for_avg else 0.0,
            4
        ),
        "threshold": confidence_threshold,
        "used_finetune": used_finetune,
        "model_name": ai_service.current_model_name,
        # 真实使用的 fine-tune 模型名 (具体某次训练的产物), 与 preview-confidence 字段对齐
        "finetune_name": mv.name if (used_finetune and mv is not None) else None,
        "base_model": mv.base_model if (used_finetune and mv is not None) else ai_service.current_model_name,
        "model_path": ai_service.current_model_path,
        "model_id": mv.id if (used_finetune and mv is not None) else None,
        # 冷启动兜底提示: 当 use_finetune 请求但无 fine-tune 时, 前端可显示
        "fallback_to_pretrained": (use_finetune and not used_finetune),
        "warning": (
            "未找到激活的 fine-tune 模型, 已自动回退到 ImageNet 预训练 (冷启动). "
            "建议: 数据集中数据足够后点击「训练」→ 等待 SUCCESS → 回到模型版本页点击「激活」,"
            "之后预标注将使用项目微调模型, 效果更好."
            if (use_finetune and not used_finetune) else None
        ),
    }


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
# 与原 preview_confidence 共享: 不写库, 不写审计, 仅返回预测结果
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
    coco_class_names: set = set()

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
                # 仅首次会下载, 后续命中本地缓存
                weights_path = f"{model_name}.pt"  # yolov8n/s/m/l/x
                coco_class_names = {n.lower() for n in YOLO(weights_path).names.values()}
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
            coco_class_names = {n.lower() for n in YOLO(weights_path).names.values()}
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
            conf_threshold=confidence_threshold,  # YOLO 内部 NMS 前 conf 过滤
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


@router.get("/list/{dataset_id}")
async def list_images(
    dataset_id: int,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    exclude_id: Optional[int] = Query(default=None, description="排除的 image id (标注工作台「下一张」用, 避免连续返回同一张)"),
    exclude_ids: Optional[str] = Query(default=None, description="批量排除的 image id 列表, 逗号分隔, 用于排除「本会话已加载但未标注」的全部图片, 防止连续点下一张回到已看过的图"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    分页查询数据集下的图片
    新增 total / status / file_size / file_url 字段, 便于前端做图像网格

    exclude_id: 排除单个 image id (单张维度, 兼容旧调用)
    exclude_ids: 批量排除, 逗号分隔, 例如 "1,2,3" (标注工作台「下一张」累积已看过的图, 避免循环回到已看过的)
    """
    # 校验数据集
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 类别映射: id -> name
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    cat_map = {c.id: c.name for c in cat_rows}

    # base 查询
    base = select(Image).where(Image.dataset_id == dataset_id)
    if status:
        base = base.where(Image.status == status)

    # 合并 exclude_id + exclude_ids, 统一用 NOT IN
    # 优先用 exclude_ids (多值), 缺失时回退到 exclude_id (单值, 兼容)
    exclude_set: set = set()
    if exclude_ids:
        for x in exclude_ids.split(','):
            x = x.strip()
            if x.isdigit():
                exclude_set.add(int(x))
    if exclude_id is not None:
        exclude_set.add(exclude_id)
    if exclude_set:
        base = base.where(Image.id.notin_(exclude_set))

    # total
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页
    stmt = base.order_by(Image.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    images = result.scalars().all()

    # v2.5.17: 批量补齐 detection/segmentation 的"实际标注数"
    # - 之前只读 image.status, 但 status 仅反映"最近一次操作", 老数据可能 status='pending'
    #   但 BBoxAnnotation / SegmentationMask 仍有残留, 导致「去标」按钮不显示
    # - 一次 SQL 拉全部 detection 图的 bbox 计数, 一次拉全部 segmentation 图的 mask 存在标记
    # - 关联到 items 上, 前端用 OR 逻辑判断"是否有标注"
    img_ids = [img.id for img in images]
    bbox_count_by_img: dict = {}
    has_mask_by_img: dict = {}
    if img_ids:
        ds_id = dataset_id
        from app.annotation.model.bbox_annotation import BBoxAnnotation
        from app.annotation.model.segmentation_mask import SegmentationMask
        det_ids_subq = select(Image.id).where(
            Image.dataset_id == ds_id,
            Image.id.in_(img_ids),
            Image.task_type == "detection",
        )
        seg_ids_subq = select(Image.id).where(
            Image.dataset_id == ds_id,
            Image.id.in_(img_ids),
            Image.task_type == "segmentation",
        )
        r = await db.execute(
            select(BBoxAnnotation.image_id, func.count(BBoxAnnotation.id))
            .where(BBoxAnnotation.image_id.in_(det_ids_subq))
            .group_by(BBoxAnnotation.image_id)
        )
        bbox_count_by_img = {row[0]: int(row[1]) for row in r.all()}
        r = await db.execute(
            select(SegmentationMask.image_id)
            .where(SegmentationMask.image_id.in_(seg_ids_subq))
        )
        has_mask_by_img = {row[0]: True for row in r.all()}

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "status_filter": status,
        "exclude_id": exclude_id,
        "exclude_ids": sorted(exclude_set) if exclude_set else None,
        "items": [
            {
                "id": img.id,
                "dataset_id": img.dataset_id,
                "filename": img.filename,
                "status": img.status,
                "task_type": img.task_type,  # v2.0.0: 冗余字段, 避免前端 join
                "width": img.width,
                "height": img.height,
                "file_size": img.file_size,
                "file_hash": img.file_hash,
                "ai_prediction": img.ai_prediction,
                "final_label_id": img.final_label_id,
                "final_label_name": cat_map.get(img.final_label_id) if img.final_label_id else None,
                "annotated_by": img.annotated_by,
                "annotated_at": img.annotated_at.isoformat() if img.annotated_at else None,
                "created_at": img.created_at.isoformat() if img.created_at else None,
                "file_url": f"/api/files/{img.id}",
                # v2.5.17: 实际标注数, 供前端「去标」按钮 / 状态徽章使用
                # - 检测: bbox 行数 (0 = 无标注)
                # - 分割: 1=有 mask, 0=无
                "bbox_count": bbox_count_by_img.get(img.id, 0),
                "has_mask": has_mask_by_img.get(img.id, False),
            }
            for img in images
        ],
    }


@router.get("/{image_id}")
async def get_image_detail(
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单图详情 (供 DatasetDetail/标注查看器使用)
    返回图片元数据 + AI 预测 + 最终类别 + 标注日志
    """
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")

    # 类别
    final_label = None
    if img.final_label_id:
        c = await db.get(Category, img.final_label_id)
        if c:
            final_label = {"id": c.id, "name": c.name, "color": c.color}

    # 标注日志
    log_rows = (await db.execute(
        select(AnnotationLog)
        .where(AnnotationLog.image_id == image_id)
        .order_by(AnnotationLog.created_at.desc())
        .limit(20)
    )).scalars().all()

    # 取所有相关类别 (供 Top-5 比对)
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == img.dataset_id)
    )).scalars().all()
    cat_map = {c.name.lower(): c.id for c in cat_rows}

    logs = []
    for log in log_rows:
        from_lab = None
        to_lab = None
        if log.from_label_id:
            fc = await db.get(Category, log.from_label_id)
            if fc:
                from_lab = {"id": fc.id, "name": fc.name}
        if log.to_label_id:
            tc = await db.get(Category, log.to_label_id)
            if tc:
                to_lab = {"id": tc.id, "name": tc.name}
        logs.append({
            "id": log.id,
            "action": log.action,
            "from_label": from_lab,
            "to_label": to_lab,
            "time_spent_ms": log.time_spent_ms,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {
        "id": img.id,
        "dataset_id": img.dataset_id,
        "filename": img.filename,
        "status": img.status,
        "task_type": img.task_type,  # v2.0.0
        "width": img.width,
        "height": img.height,
        "file_size": img.file_size,
        "file_hash": img.file_hash,
        "ai_prediction": img.ai_prediction,
        "final_label": final_label,
        "annotated_by": img.annotated_by,
        "annotated_at": img.annotated_at.isoformat() if img.annotated_at else None,
        "created_at": img.created_at.isoformat() if img.created_at else None,
        "file_url": f"/api/files/{img.id}",
        "annotation_history": logs,
    }


@router.delete("/{image_id}")
async def delete_image(
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    删除单张图片 (v3.0.0 Phase 4: thin wrapper, 业务下沉到 ImageService.delete)

    业务规则 (全部在 service 层):
    - 删文件 (storage_service)
    - 删 AnnotationLog (CASCADE 已配)
    - 删 BBoxAnnotation / SegmentationMask (ORM cascade='all, delete-orphan')
    - 更新 dataset.image_count / annotated_count

    关键: 必须 eager load 关联 (bbox_annotations / segmentation_mask), 否则
    SQLAlchemy 的 ORM cascade 不会触发, bbox/mask 会残留
    """
    from sqlalchemy.orm import selectinload
    stmt = (
        select(Image)
        .where(Image.id == image_id)
        .options(
            selectinload(Image.bbox_annotations),
            selectinload(Image.segmentation_mask),
        )
    )
    img = (await db.execute(stmt)).scalar_one_or_none()
    if not img:
        raise HTTPException(404, "Image not found")

    return await ImageService.delete(db, img)


@router.post("/batch-delete")
async def batch_delete_images(
    image_ids: List[int],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量删除图片 (v3.0.0 Phase 4: thin wrapper, 业务下沉到 ImageService.batch_delete)
    Body: { "image_ids": [1, 2, 3] } (或直接数组)
    """
    return await ImageService.batch_delete(db, image_ids)
