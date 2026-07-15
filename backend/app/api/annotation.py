"""
Annotation API: Human Correction
================================
核心创新点接口: 人工确认 / 修正 AI 预标注
"""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from pydantic import BaseModel

from app.database import get_db
from app.models.image import Image
from app.models.dataset import Dataset
from app.models.category import Category
from app.models.annotation_log import AnnotationLog
from app.models.user import User
from app.core.deps import get_current_user

router = APIRouter()


class AnnotateRequest(BaseModel):
    image_id: int
    label_id: int
    time_spent_ms: int = 0
    is_confirm: bool = False  # True=确认 AI 标注, False=修正 AI 标注


@router.post("/save")
async def save_annotation(
    req: AnnotateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    保存人工标注结果
    - 写入 image.final_label_id
    - 写入 annotation_log 审计
    - 更新 image.status
    """
    img = await db.get(Image, req.image_id)
    if not img:
        raise HTTPException(404, "Image not found")

    label = await db.get(Category, req.label_id)
    if not label:
        raise HTTPException(404, "Category not found")

    # 记录原人工标注 (供审计, 即使没 AI 预测也要记)
    from_label_id = img.final_label_id

    # 更新图片标注
    img.final_label_id = req.label_id
    img.annotated_by = current_user.id
    img.annotated_at = datetime.utcnow()
    img.status = "human_confirmed" if req.is_confirm else "human_corrected"

    # 写审计日志
    # 修复: 之前只对有 ai_prediction 的图记 from_label_id, 导致修改标注时旧 label 丢失
    # 现在的规则:
    #   - 首次标注 (from_label_id 为空): 记 "confirm" 或 "correct", from_label_id 留空
    #   - 修改标注 (from_label_id 不为空): 强制记 "correct" (与 is_confirm 无关, 因为确实是改了)
    is_modify = from_label_id is not None
    action = "correct" if is_modify else ("confirm" if req.is_confirm else "correct")
    log = AnnotationLog(
        image_id=req.image_id,
        user_id=current_user.id,
        action=action,
        from_label_id=from_label_id,
        to_label_id=req.label_id,
        time_spent_ms=req.time_spent_ms,
    )
    db.add(log)

    # 更新类别样本数
    # 修改标注: 旧类目 -1, 新类目 +1
    if is_modify and from_label_id != req.label_id:
        old_label = await db.get(Category, from_label_id)
        if old_label:
            old_label.sample_count = max(0, (old_label.sample_count or 0) - 1)
    label.sample_count = (label.sample_count or 0) + 1

    await db.commit()

    return {
        "success": True,
        "image_id": req.image_id,
        "new_status": img.status,
        "label": label.name,
    }


class ClearAnnotationsRequest(BaseModel):
    image_ids: List[int]


@router.post("/clear")
async def clear_annotations(
    req: ClearAnnotationsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量去除图片的人工标注 (final_label_id) 和 AI 预标注 (ai_prediction)
    - 支持单张 [id] 或多张 [id, id, ...]
    - 对以下任一情况都会处理:
        * status ∈ {human_confirmed, human_corrected, trained}: 清 final_label_id + 审计 + 扣 annotated_count
        * status = "ai_labeled" 且有 ai_prediction: 清 ai_prediction, 状态回 pending
        * final_label_id 存在但状态异常: 保守按人工处理, 清 final_label_id
    - 写一条 annotation_log action="reject" 留痕
    - 对应 Category.sample_count - 1
    - 不会真的删除图片
    """
    ids = [int(x) for x in (req.image_ids or []) if x is not None]
    if not ids:
        raise HTTPException(400, "image_ids cannot be empty")
    if len(ids) > 500:
        raise HTTPException(400, "Too many ids (max 500)")

    # 1) 查图
    stmt = select(Image).where(Image.id.in_(ids))
    images = (await db.execute(stmt)).scalars().all()
    if not images:
        return {"success": True, "cleared": 0, "skipped": len(ids), "items": []}

    cleared = 0
    skipped = 0
    details: List[dict] = []
    # 按 dataset / category 聚合, 减少 SQL 次数
    annotated_delta_by_ds: dict = {}
    sample_delta_by_cat: dict = {}

    for img in images:
        had_label = img.final_label_id is not None
        had_human = img.status in ("human_confirmed", "human_corrected", "trained")
        had_ai = img.status == "ai_labeled" and img.ai_prediction is not None

        if not had_label and not had_human and not had_ai:
            skipped += 1
            details.append({
                "image_id": img.id, "filename": img.filename,
                "result": "skipped", "reason": "no annotation"
            })
            continue

        old_label_id = img.final_label_id
        old_ai_top1 = (img.ai_prediction or {}).get("top1") if had_ai else None

        # 清空人工标注
        img.final_label_id = None
        img.annotated_by = None
        img.annotated_at = None

        # 清空 AI 预标注
        if had_ai:
            img.ai_prediction = None
            img.status = "pending"
        elif had_human:
            img.status = "pending"
        else:
            # 仅有 final_label_id 但状态异常 (例如数据库被手工改过), 保守回 pending
            img.status = "pending"

        # 写审计
        log = AnnotationLog(
            image_id=img.id,
            user_id=current_user.id,
            action="reject",
            from_label_id=old_label_id,
            to_label_id=None,
            time_spent_ms=0,
        )
        db.add(log)

        # 聚合计数变化
        if had_human:
            annotated_delta_by_ds[img.dataset_id] = (
                annotated_delta_by_ds.get(img.dataset_id, 0) + 1
            )
        if old_label_id:
            sample_delta_by_cat[old_label_id] = (
                sample_delta_by_cat.get(old_label_id, 0) + 1
            )

        cleared += 1
        details.append({
            "image_id": img.id,
            "filename": img.filename,
            "result": "cleared",
            "old_label_id": old_label_id,
            "had_ai": had_ai,
            "ai_cleared": had_ai,
            "old_ai_top1": old_ai_top1,
        })

    # 2) 扣减 category.sample_count (下限 0)
    for cat_id, delta in sample_delta_by_cat.items():
        from sqlalchemy import case as sa_case
        new_cnt = sa_case(
            (Category.sample_count - delta < 0, 0),
            else_=Category.sample_count - delta,
        )
        await db.execute(
            update(Category).where(Category.id == cat_id).values(sample_count=new_cnt)
        )

    # 3) 扣减 dataset.annotated_count (下限 0)
    for ds_id, delta in annotated_delta_by_ds.items():
        from sqlalchemy import case as sa_case
        new_cnt = sa_case(
            (Dataset.annotated_count - delta < 0, 0),
            else_=Dataset.annotated_count - delta,
        )
        await db.execute(
            update(Dataset).where(Dataset.id == ds_id).values(annotated_count=new_cnt)
        )

    await db.commit()

    return {
        "success": True,
        "cleared": cleared,
        "skipped": skipped,
        "missing": len(ids) - len(images),
        "items": details,
    }


@router.get("/stats/{dataset_id}")
async def annotation_stats(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    标注效率统计 (论文核心图表数据源)
    - 各状态图片数
    - AI 节省时间估算
    - 标注员人均速度
    """
    from sqlalchemy import func
    # 各状态图片数
    stmt = (
        select(Image.status, func.count(Image.id))
        .where(Image.dataset_id == dataset_id)
        .group_by(Image.status)
    )
    status_counts = dict((await db.execute(stmt)).all())

    # 标注总耗时
    stmt = (
        select(func.sum(AnnotationLog.time_spent_ms), func.count(AnnotationLog.id))
        .join(Image, Image.id == AnnotationLog.image_id)
        .where(Image.dataset_id == dataset_id)
    )
    total_time, total_annos = (await db.execute(stmt)).one()

    # 估算 AI 节省时间 (假设 AI 标注平均节省 3 秒/张)
    ai_labeled = status_counts.get("ai_labeled", 0) + status_counts.get("human_confirmed", 0)
    estimated_saved_seconds = ai_labeled * 3

    return {
        "status_counts": status_counts,
        "total_annotations": total_annos,
        "total_time_seconds": (total_time or 0) // 1000,
        "avg_seconds_per_image": round((total_time or 0) / 1000 / max(total_annos, 1), 2),
        "ai_labeled_count": ai_labeled,
        "estimated_saved_seconds": estimated_saved_seconds,
    }


@router.get("/list/{dataset_id}")
async def list_annotations(
    dataset_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    action: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    标注审计日志列表 (按数据集)
    - 分页 + 按 action 过滤 (confirm / correct)
    - 返回图片名 + 用户名 + 耗时 + 时间
    """
    from app.models.user import User as UserModel
    base = (
        select(AnnotationLog, Image.filename, UserModel.username)
        .join(Image, Image.id == AnnotationLog.image_id)
        .join(UserModel, UserModel.id == AnnotationLog.user_id)
        .where(Image.dataset_id == dataset_id)
    )
    if action:
        base = base.where(AnnotationLog.action == action)

    # total
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页
    stmt = base.order_by(AnnotationLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).all()

    items = []
    for log, filename, username in rows:
        from_lab = None
        to_lab = None
        if log.from_label_id:
            fc = await db.get(Category, log.from_label_id)
            from_lab = fc.name if fc else None
        if log.to_label_id:
            tc = await db.get(Category, log.to_label_id)
            to_lab = tc.name if tc else None
        items.append({
            "id": log.id,
            "image_id": log.image_id,
            "image_filename": filename,
            "user_id": log.user_id,
            "username": username,
            "action": log.action,
            "from_label_name": from_lab,
            "to_label_name": to_lab,
            "time_spent_ms": log.time_spent_ms,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


@router.get("/recent")
async def recent_annotations(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    全系统最近 N 条标注 (Dashboard 活动流)
    """
    from app.models.user import User as UserModel
    stmt = (
        select(AnnotationLog, Image.filename, Image.dataset_id, UserModel.username)
        .join(Image, Image.id == AnnotationLog.image_id)
        .join(UserModel, UserModel.id == AnnotationLog.user_id)
        .order_by(AnnotationLog.id.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    items = []
    for log, filename, ds_id, username in rows:
        to_lab = None
        if log.to_label_id:
            tc = await db.get(Category, log.to_label_id)
            to_lab = tc.name if tc else None
        items.append({
            "id": log.id,
            "image_id": log.image_id,
            "image_filename": filename,
            "dataset_id": ds_id,
            "username": username,
            "action": log.action,
            "to_label_name": to_lab,
            "time_spent_ms": log.time_spent_ms,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })
    return {"items": items}
