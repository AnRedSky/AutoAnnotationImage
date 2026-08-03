"""
image.auto_label 模块 — 批量自动标注 API
========================================

v3.0.0 Phase N 拆分: 从 image.py 抽离
- 职责: 批量自动标注 (写库, 写 ai_predict 审计)
- 模型选择:
  - use_finetune=True (默认): 优先 fine-tune, 冷启动兜底 ImageNet
  - use_finetune=False: 严格模式, 有 fine-tune 时禁止
- 输出: ai_prediction + status=ai_labeled + final_label_id

v3.0.0 不合格虚拟类别支持:
- 优先用 mv.class_names 构建 idx→name 映射 (含 __unqualified__ 末位虚拟类别)
- 推理命中 __unqualified__ 且置信度 >= threshold → 自动 mark_unqualified
  (不写 final_label_id, status 保持 pending, 仅 quality_flag="unqualified")
- 旧模型无 class_names → fallback 到 Category 表 sorted (向后兼容)
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.category import Category
from app.tasks.model.annotation_log import AnnotationLog
from app.tasks.model.model_version import ModelVersion
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.common.ml.ai_service import ai_service
from app.common.ml.ai_service import filter_predictions_to_categories
from app.common.enums import UNQUALIFIED_LABEL, RejectReason
from app.core.config import settings

# auto_label 独立 router
router = APIRouter()


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

    v3.0.0: 若 fine- v3.0.0: 若 fine-tune 模型训练时纳入了 __unqualified__ 虚拟类别 (mv.class_names 末位含
    __unqualified__), 推理命中该类别且置信度 >= threshold 的图片会被自动标记为不合格
    (quality_flag="unqualified", reject_reason="ai_detected"), 不写 final_label_id.

    v3.3.0 P0 修复: 必须校验写权限
    """
    # v3.3.0 P0: 权限校验
    # - 与 delete.py 等 v3.3.0 修复保持一致: Dataset / 权限服务放在函数内
    #   导入, 避免顶层导入引发循环依赖
    from app.tasks.model.dataset import Dataset
    from app.tasks.service.permission_service import assert_can_access_dataset
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    await assert_can_access_dataset(db, current_user, ds, require_write=True)

    # 1. 加载模型
    used_finetune = False
    mv: Optional[ModelVersion] = None
    # v3.0.0: 是否启用了不合格虚拟类别 (mv.class_names 含 __unqualified__)
    has_unqualified_class = False
    if use_finetune:
        # 取 fine-tune 模型: 优先 model_id 显式指定, 否则取激活
        if model_id is not None:
            mv = (await db.execute(
                select(ModelVersion).where(ModelVersion.id == model_id)
            )).scalars().first()
            if mv is None:
                raise HTTPException(404, f"ModelVersion id={model_id} not found")
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
            # v3.0.0: 优先用 mv.class_names (训练时存储, 含 __unqualified__ 虚拟类别)
            # 旧模型 class_names 为 NULL → fallback 到 Category 表 sorted (向后兼容)
            if isinstance(mv.class_names, list) and mv.class_names:
                label_names_for_map = list(mv.class_names)
                has_unqualified_class = UNQUALIFIED_LABEL in label_names_for_map
            else:
                label_names_for_map = sorted({c.name for c in cats})
                has_unqualified_class = False
            ai_service.set_label_map({i: name for i, name in enumerate(label_names_for_map)})
            used_finetune = True
        else:
            # 冷启动兜底: 没有激活的 fine-tune 模型, 自动回退到 timm ImageNet 预训练
            ai_service.set_label_map(None)
            if ai_service.current_model_name != model_name:
                try:
                    await ai_service.load_pretrained(model_name)
                except Exception as e:
                    raise HTTPException(500, f"Failed to load pretrained model: {e}")
            used_finetune = False
    else:
        # 严格模式兜底: 即使用户显式 use_finetune=False, 也只在项目**完全没 fine-tune** 时允许
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
            "used_finetune": used_finetune,
            "model_name": ai_service.current_model_name,
            "model_path": ai_service.current_model_path,
            "model_id": mv.id if (used_finetune and mv is not None) else None,
            "fallback_to_pretrained": (use_finetune and not used_finetune),
            "has_unqualified_class": has_unqualified_class,
            "auto_marked_unqualified": 0,
            "message": "No pending images",
        }

    # 3. 批量推理
    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    predictions = await ai_service.batch_predict(image_paths, top_k=5)

    # 4. 写回数据库
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    name_to_id = {c.name: c.id for c in cat_rows}
    category_names = [c.name for c in cat_rows]

    # use_finetune=False (或回退到 ImageNet) 时, base model 输出可能是 class_579 / tabby_cat
    # 等与项目类目无关的标签. 必须过滤到只含项目类目, 无匹配的图保持 pending 不标注
    if used_finetune:
        final_predictions = predictions
    else:
        final_predictions = filter_predictions_to_categories(predictions, category_names)

    auto_labeled = 0
    no_match = 0
    # v3.0.0: 自动标记为不合格的图片数 (命中 __unqualified__ 类别)
    auto_marked_unqualified = 0
    confs_for_avg: list = []
    for img, pred in zip(images, final_predictions):
        if pred is None:
            img.ai_prediction = None
            no_match += 1
            continue
        img.ai_prediction = pred
        confs_for_avg.append(pred["top1_conf"])

        # v3.0.0: 优先识别不合格虚拟类别 (命中 → mark_unqualified, 不走正常标注流程)
        top1_label = pred.get("top1")
        if (
            has_unqualified_class
            and top1_label == UNQUALIFIED_LABEL
            and pred["top1_conf"] >= confidence_threshold
            and not img.is_unqualified()
        ):
            img.mark_unqualified(current_user.id, RejectReason.AI_DETECTED.value)
            db.add(AnnotationLog(
                image_id=img.id,
                user_id=current_user.id,
                action="mark_unqualified",
                payload={
                    "reason": RejectReason.AI_DETECTED.value,
                    "source": "auto_label",
                    "model_id": mv.id if mv else None,
                    "confidence": pred["top1_conf"],
                },
                time_spent_ms=0,
            ))
            auto_marked_unqualified += 1
            continue

        # 正常标注流程
        if pred["top1_conf"] >= confidence_threshold:
            img.status = "ai_labeled"
            if top1_label and top1_label in name_to_id:
                img.final_label_id = name_to_id[top1_label]
            db.add(AnnotationLog(
                image_id=img.id,
                user_id=current_user.id,
                action="ai_predict",
                from_label_id=None,
                to_label_id=img.final_label_id,
                time_spent_ms=0,
            ))
            auto_labeled += 1

    await db.commit()

    return {
        "total": len(images),
        "auto_labeled": auto_labeled,
        "need_human": len(images) - auto_labeled - auto_marked_unqualified,
        "no_match": no_match,
        "avg_confidence": round(
            (sum(confs_for_avg) / len(confs_for_avg)) if confs_for_avg else 0.0,
            4
        ),
        "threshold": confidence_threshold,
        "used_finetune": used_finetune,
        "model_name": ai_service.current_model_name,
        "finetune_name": mv.name if (used_finetune and mv is not None) else None,
        "base_model": mv.base_model if (used_finetune and mv is not None) else ai_service.current_model_name,
        "model_path": ai_service.current_model_path,
        "model_id": mv.id if (used_finetune and mv is not None) else None,
        "fallback_to_pretrained": (use_finetune and not used_finetune),
        # v3.0.0: 不合格虚拟类别训练 / 推理元信息
        "has_unqualified_class": has_unqualified_class,
        "auto_marked_unqualified": auto_marked_unqualified,
        "warning": (
            "未找到激活的 fine-tune 模型, 已自动回退到 ImageNet 预训练 (冷启动). "
            "建议: 数据集中数据足够后点击「训练」→ 等待 SUCCESS → 回到模型版本页点击「激活」,"
            "之后预标注将使用项目微调模型, 效果更好."
            if (use_finetune and not used_finetune) else None
        ),
    }
