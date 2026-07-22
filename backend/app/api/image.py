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
from app.models.image import Image
from app.models.dataset import Dataset
from app.models.category import Category
from app.models.annotation_log import AnnotationLog
from app.models.user import User
from app.core.deps import get_current_user
from app.services import ai_service
from app.services.ai_service import filter_predictions_to_categories
from app.services.storage_service import storage_service
from app.config import settings
from app.models.model_version import ModelVersion

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

    用途: 在点击「启动 AI 预标注」之前, 让用户先看到「如果现在跑批量预标注, 哪些图会被标、哪些会留在待标注」
    - **完全不改数据库** (不写 ai_prediction, 不改 status)
    - 不写 AnnotationLog 审计
    - 用户可反复调整阈值/模型预览, 选定后再点击批量预标注

    加载模型逻辑与 auto_label 保持一致 (fine-tune 优先, 无则冷启动 timm 兜底)
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
            # 早 return 也补齐 finetune_name / base_model 字段, 前端模板能稳定渲染
            "finetune_name": None,
            "base_model": None,
            "model_id": None,
            "fallback_to_pretrained": False,
        }

    # 2. 加载模型 (与 auto_label 完全一致, 不写库)
    used_finetune = False
    mv: Optional[ModelVersion] = None
    if use_finetune:
        if model_id is not None:
            mv = (await db.execute(
                select(ModelVersion).where(ModelVersion.id == model_id)
            )).scalars().first()
        if mv is None:
            mv = (await db.execute(
                select(ModelVersion).where(ModelVersion.is_active == True)  # noqa: E712
                .order_by(ModelVersion.id.desc()).limit(1)
            )).scalars().first()
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
                    400,
                    "Dataset has no categories. Please add categories first (Dataset -> 类别)."
                )
            sorted_names = sorted({c.name for c in cats})
            ai_service.set_label_map({i: name for i, name in enumerate(sorted_names)})
            used_finetune = True
        else:
            # 冷启动兜底
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
                400,
                "Project has fine-tune models. Preview must use fine-tune models. "
                "Set use_finetune=True to use the active fine-tune model."
            )
        ai_service.set_label_map(None)
        if ai_service.current_model_name != model_name:
            try:
                await ai_service.load_pretrained(model_name)
            except Exception as e:
                raise HTTPException(500, f"Failed to load model: {e}")
        used_finetune = False

    # 3. 批量推理 (不写库, 只拿结果)
    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    predictions = await ai_service.batch_predict(image_paths, top_k=5)

    # 4. 计算"是否会被自动标注"
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    category_names = [c.name for c in cat_rows]

    # 与 auto_label 同样的过滤: use_finetune=False / 冷启动时, base 输出需要过滤到项目类目
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
            # 模型输出与项目类目无交集: 既不标注也无需人工二次确认, 走 "no_match"
            items.append({
                "image_id": img.id,
                "filename": img.filename,
                "thumb_url": f"/api/files/{img.id}/preview",
                "top1": None,
                "top1_conf": None,
                "candidates": [],
                "in_project_categories": False,
                "would_label": False,
                "reason": "no_match",
            })
            no_match += 1
            continue
        top1 = pred.get("top1")
        top1_conf = float(pred.get("top1_conf") or 0.0)
        in_proj = (top1 in category_names) if top1 else False
        would = (top1_conf >= confidence_threshold) and in_proj
        items.append({
            "image_id": img.id,
            "filename": img.filename,
            "thumb_url": f"/api/files/{img.id}/preview",
            "top1": top1,
            "top1_conf": round(top1_conf, 4),
            "candidates": [
                {"label": c.get("label"), "conf": round(float(c.get("conf") or 0.0), 4)}
                for c in (pred.get("candidates") or [])
            ],
            "in_project_categories": in_proj,
            "would_label": would,
            "reason": "would_label" if would else (
                "below_threshold" if not (top1_conf >= confidence_threshold) else "not_in_categories"
            ),
        })
        if would:
            would_label += 1
        else:
            need_human += 1

    return {
        "items": items,
        "would_label": would_label,
        "need_human": need_human,
        "no_match": no_match,
        "total": len(items),
        "threshold": confidence_threshold,
        # model_name 是 ai_service 加载的 timm 模型名 (base_model, e.g. "resnet50")
        "model_name": ai_service.current_model_name,
        # 真实测评的 fine-tune 模型名 (具体某次训练的产物, e.g. "resnet50_v1_1784124522")
        # 前端用这个显示"测评的是哪个具体微调模型", 避免只看到基础模型名 + fine-tune tag 困惑
        "finetune_name": mv.name if (used_finetune and mv is not None) else None,
        "base_model": mv.base_model if (used_finetune and mv is not None) else ai_service.current_model_name,
        "model_id": mv.id if (used_finetune and mv is not None) else None,
        "used_finetune": used_finetune,
        "fallback_to_pretrained": (use_finetune and not used_finetune),
        "warning": (
            "未找到激活的 fine-tune 模型, 已自动回退到 ImageNet 预训练 (冷启动). "
            "建议训练并激活 fine-tune 模型以获得更准的预览效果."
            if (use_finetune and not used_finetune) else None
        ),
    }


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
        from app.models.bbox_annotation import BBoxAnnotation
        from app.models.segmentation_mask import SegmentationMask
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
    删除单张图片
    - 删文件
    - 删 AnnotationLog (CASCADE 已配)
    - 删 BBoxAnnotation / SegmentationMask (v2.0.0: ORM cascade='all, delete-orphan')
    - 更新 dataset.image_count / annotated_count

    关键: 必须 eager load 关联 (bbox_annotations / segmentation_mask), 否则
    SQLAlchemy 的 ORM cascade 不会触发, bbox/mask 会残留 (即使 DB 层有
    ON DELETE CASCADE, SQLite 默认不开启 PRAGMA foreign_keys, 也不可靠)
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

    dataset_id = img.dataset_id
    was_annotated = img.status in ("human_confirmed", "human_corrected", "trained")

    # 删文件
    try:
        if storage_service.exists(img.storage_path):
            await storage_service.delete(img.storage_path)
    except Exception:
        pass

    # 删 DB
    await db.delete(img)
    # 用 SQLAlchemy case 替代 MySQL 专属的 GREATEST, 兼容 SQLite/PostgreSQL
    new_img_count = case(
        (Dataset.image_count - 1 < 0, 0),
        else_=Dataset.image_count - 1,
    )
    await db.execute(
        update(Dataset)
        .where(Dataset.id == dataset_id)
        .values(image_count=new_img_count)
    )
    if was_annotated:
        new_ann_count = case(
            (Dataset.annotated_count - 1 < 0, 0),
            else_=Dataset.annotated_count - 1,
        )
        await db.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id)
            .values(annotated_count=new_ann_count)
        )
    await db.commit()

    return {"success": True, "deleted_id": image_id}


@router.post("/batch-delete")
async def batch_delete_images(
    image_ids: List[int],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量删除图片
    Body: { "image_ids": [1, 2, 3] } (或直接数组)
    """
    if not image_ids:
        raise HTTPException(400, "image_ids cannot be empty")
    if len(image_ids) > 500:
        raise HTTPException(400, "Too many ids (max 500)")

    # 1) 找出所有需要删除的图
    stmt = select(Image).where(Image.id.in_(image_ids))
    images = (await db.execute(stmt)).scalars().all()
    if not images:
        return {"success": True, "deleted": 0, "missing": len(image_ids)}

    # 2) 按 dataset 分组 (更新计数)
    by_dataset: dict = {}
    annotated_count_by_ds: dict = {}
    for img in images:
        by_dataset[img.dataset_id] = by_dataset.get(img.dataset_id, 0) + 1
        if img.status in ("human_confirmed", "human_corrected", "trained"):
            annotated_count_by_ds[img.dataset_id] = (
                annotated_count_by_ds.get(img.dataset_id, 0) + 1
            )

    # 3) 删文件
    deleted = 0
    for img in images:
        try:
            if storage_service.exists(img.storage_path):
                await storage_service.delete(img.storage_path)
            deleted += 1
        except Exception:
            pass

    # 4) 删 DB
    await db.execute(delete(Image).where(Image.id.in_([i.id for i in images])))
    # 用 SQLAlchemy case 替代 MySQL 专属的 GREATEST, 兼容 SQLite/PostgreSQL
    for ds_id, cnt in by_dataset.items():
        new_img = case(
            (Dataset.image_count - cnt < 0, 0),
            else_=Dataset.image_count - cnt,
        )
        await db.execute(
            update(Dataset)
            .where(Dataset.id == ds_id)
            .values(image_count=new_img)
        )
    for ds_id, cnt in annotated_count_by_ds.items():
        new_ann = case(
            (Dataset.annotated_count - cnt < 0, 0),
            else_=Dataset.annotated_count - cnt,
        )
        await db.execute(
            update(Dataset)
            .where(Dataset.id == ds_id)
            .values(annotated_count=new_ann)
        )
    await db.commit()

    return {
        "success": True,
        "deleted": deleted,
        "missing": len(image_ids) - len(images),
    }
