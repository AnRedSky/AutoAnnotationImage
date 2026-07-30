"""
Annotation API: Human Correction
================================
核心创新点接口: 人工确认 / 修正 AI 预标注

v2.5.16 重大修复 (DatasetDetail 去标):
- /clear 接口原本只清 final_label_id (分类) + ai_prediction + image.status
- 检测 (BBoxAnnotation 表) / 分割 (SegmentationMask 表) 数据完全没动
- 后果: DatasetDetail 的"去标"按钮对检测/分割是 noop, 训练/导出仍带旧数据
- 修复: 按 img.task_type 分派, 分类清 final_label, 检测删 bbox, 分割删 mask + 物理文件
"""
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete
from pydantic import BaseModel

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.tasks.model.annotation_log import AnnotationLog
from app.admin.model.user import User
# v2.5.16: 引入检测 / 分割的 ORM 模型, 用于按 task_type 清理
from app.annotation.model.bbox_annotation import BBoxAnnotation
from app.annotation.model.segmentation_mask import SegmentationMask
from app.common.storage.storage_service import storage_service
from app.middleware.http.auth import get_current_user

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

    # P0-4: 非 admin 只能标注自己 dataset 的图片
    from app.tasks.model.dataset import Dataset
    ds = await db.get(Dataset, img.dataset_id)
    if not ds or (not current_user.is_admin() and ds.owner_id != current_user.id):
        raise HTTPException(403, "无权限标注此数据集的图片")

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

    # v3.0.0: 写入新标注时自动清除"不合格"标记
    # - 业务规则: 类别标签与不合格标记互斥, 标注了类别即视为有效图片
    # - 写一条 unmark_unqualified 审计日志, 保留撤销原因
    auto_unmarked = False
    if img.is_unqualified():
        img.unmark_unqualified()
        auto_unmarked = True

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
    if auto_unmarked:
        # 写一条 unmark_unqualified 审计 (自动撤销原因: 标注了类别)
        db.add(AnnotationLog(
            image_id=req.image_id,
            user_id=current_user.id,
            action="unmark_unqualified",
            payload={"reason": "auto_cleared_on_label_save"},
            time_spent_ms=0,
        ))

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
        # v3.0.0: 标注保存时, 不合格标记被自动清除, 前端需同步清空本地 image.value
        "auto_unmarked_unqualified": auto_unmarked,
        "quality_flag": img.quality_flag,  # 现在一定是 None
    }


class ClearAnnotationsRequest(BaseModel):
    image_ids: List[int]


# ============== 不合格图片标记 (v3.0.0 新增, 正交于 status 状态机) ==============

class MarkUnqualifiedRequest(BaseModel):
    """标记单张图片为不合格"""
    image_id: int
    reason: str  # 预设枚举值 (见 REJECT_REASON_VALUES)
    custom_text: Optional[str] = None  # reason="other" 时的自定义文本


class UnmarkUnqualifiedRequest(BaseModel):
    """撤销单张图片的不合格标记"""
    image_id: int


class BatchMarkUnqualifiedRequest(BaseModel):
    """批量标记图片为不合格"""
    image_ids: List[int]
    reason: str
    custom_text: Optional[str] = None


@router.post("/mark-unqualified")
async def mark_unqualified(
    req: MarkUnqualifiedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """标记单张图片为不合格

    - 设置 Image.quality_flag / reject_reason / rejected_by / rejected_at
    - 不修改 Image.status / final_label_id (保留原标注状态, 撤销可恢复)
    - 写 AnnotationLog(action=mark_unqualified, payload={reason, custom_text}) 留痕
    - 不扣减 Category.sample_count (标注信息保留, 仅作质量标记)
    """
    from app.common.enums import REJECT_REASON_VALUES
    if req.reason not in REJECT_REASON_VALUES:
        raise HTTPException(400, f"非法原因: {req.reason}, 可选: {list(REJECT_REASON_VALUES)}")

    img = await db.get(Image, req.image_id)
    if not img:
        raise HTTPException(404, "Image not found")

    if img.is_unqualified():
        raise HTTPException(409, f"图片 {req.image_id} 已被标记为不合格")

    img.mark_unqualified(current_user.id, req.reason)

    payload: dict = {"reason": req.reason}
    if req.custom_text:
        payload["custom_text"] = req.custom_text
    log = AnnotationLog(
        image_id=req.image_id,
        user_id=current_user.id,
        action="mark_unqualified",
        payload=payload,
        time_spent_ms=0,
    )
    db.add(log)
    await db.commit()

    return {
        "success": True,
        "image_id": req.image_id,
        "quality_flag": img.quality_flag,
        "reject_reason": img.reject_reason,
    }


@router.post("/unmark-unqualified")
async def unmark_unqualified(
    req: UnmarkUnqualifiedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销单张图片的不合格标记

    - 清空 Image.quality_flag / reject_reason / rejected_by / rejected_at
    - 不恢复 (也不修改) Image.status (原标注状态一直保留, 无需恢复)
    - 写 AnnotationLog(action=unmark_unqualified) 留痕
    """
    img = await db.get(Image, req.image_id)
    if not img:
        raise HTTPException(404, "Image not found")

    if not img.is_unqualified():
        raise HTTPException(409, f"图片 {req.image_id} 未被标记为不合格")

    img.unmark_unqualified()

    log = AnnotationLog(
        image_id=req.image_id,
        user_id=current_user.id,
        action="unmark_unqualified",
        time_spent_ms=0,
    )
    db.add(log)
    await db.commit()

    return {"success": True, "image_id": req.image_id, "quality_flag": None}


@router.post("/batch-mark-unqualified")
async def batch_mark_unqualified(
    req: BatchMarkUnqualifiedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量标记图片为不合格 (参考 /clear 的批量模式)

    - 一次事务处理多张图, 任一不存在则跳过 (不回滚)
    - 已标记为不合格的图跳过 (幂等)
    - 每张图写一条 AnnotationLog(action=mark_unqualified)
    """
    from app.common.enums import REJECT_REASON_VALUES
    if req.reason not in REJECT_REASON_VALUES:
        raise HTTPException(400, f"非法原因: {req.reason}, 可选: {list(REJECT_REASON_VALUES)}")

    ids = [int(x) for x in (req.image_ids or []) if x is not None]
    if not ids:
        raise HTTPException(400, "image_ids cannot be empty")
    if len(ids) > 500:
        raise HTTPException(400, "Too many ids (max 500)")

    stmt = select(Image).where(Image.id.in_(ids))
    images = (await db.execute(stmt)).scalars().all()

    payload: dict = {"reason": req.reason}
    if req.custom_text:
        payload["custom_text"] = req.custom_text

    marked = 0
    skipped = 0
    details: List[dict] = []
    for img in images:
        if img.is_unqualified():
            skipped += 1
            details.append({"image_id": img.id, "filename": img.filename, "result": "skipped", "reason": "already unqualified"})
            continue
        img.mark_unqualified(current_user.id, req.reason)
        db.add(AnnotationLog(
            image_id=img.id,
            user_id=current_user.id,
            action="mark_unqualified",
            payload=payload,
            time_spent_ms=0,
        ))
        marked += 1
        details.append({"image_id": img.id, "filename": img.filename, "result": "marked"})

    await db.commit()

    return {
        "success": True,
        "marked": marked,
        "skipped": skipped,
        "missing": len(ids) - len(images),
        "items": details,
    }


@router.post("/clear")
async def clear_annotations(
    req: ClearAnnotationsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量去除图片的人工标注 (final_label_id) 和 AI 预标注 (ai_prediction)
    v2.5.16: 同时清理检测 (BBoxAnnotation) 和 分割 (SegmentationMask) 数据 + 物理文件

    - 支持单张 [id] 或多张 [id, id, ...]
    - 按 img.task_type 分派清理:
        * classification: 清 final_label_id / ai_prediction / status -> pending
        * detection:      删 BBoxAnnotation 行 (按 image_id)
        * segmentation:   删 SegmentationMask 行 + 物理 mask 文件
    - 任一标注存在就处理: final_label_id / status in human_*/trained / ai_prediction / BBoxAnnotation / SegmentationMask
    - 写一条 annotation_log action="reject" 留痕 (分类场景)
    - 对应 Category.sample_count - 1 (分类场景)
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

    # v2.5.16: 预查询检测/分割数据, 用于"实际有数据"的判断
    # - 检测: 统计每张图的 bbox 行数 {image_id: count}
    # - 分割: 取出每张图的 mask 元信息 {image_id: SegmentationMask}
    # 一次 SQL 拉全部, 避免循环内 N+1
    bbox_count_by_img: dict = {}
    mask_by_img: dict = {}
    detection_ids = [img.id for img in images if img.task_type == "detection"]
    segmentation_ids = [img.id for img in images if img.task_type == "segmentation"]
    if detection_ids:
        r = await db.execute(
            select(BBoxAnnotation.image_id, func.count(BBoxAnnotation.id))
            .where(BBoxAnnotation.image_id.in_(detection_ids))
            .group_by(BBoxAnnotation.image_id)
        )
        bbox_count_by_img = {row[0]: int(row[1]) for row in r.all()}
    if segmentation_ids:
        r = await db.execute(
            select(SegmentationMask).where(SegmentationMask.image_id.in_(segmentation_ids))
        )
        for m in r.scalars().all():
            mask_by_img[m.image_id] = m

    cleared = 0
    skipped = 0
    bbox_cleared_total = 0  # v2.5.16: 总共删的 bbox 行数
    mask_cleared_total = 0  # v2.5.16: 总共删的 mask 行数
    details: List[dict] = []
    # 按 dataset / category 聚合, 减少 SQL 次数
    annotated_delta_by_ds: dict = {}
    sample_delta_by_cat: dict = {}

    for img in images:
        had_label = img.final_label_id is not None
        had_human = img.status in ("human_confirmed", "human_corrected", "trained")
        had_ai = img.status == "ai_labeled" and img.ai_prediction is not None
        # v2.5.16: 检测/分割的"实际有标注"判定
        bbox_count = bbox_count_by_img.get(img.id, 0)
        had_bbox = bbox_count > 0
        mask_obj = mask_by_img.get(img.id)
        had_mask = mask_obj is not None

        if not (had_label or had_human or had_ai or had_bbox or had_mask):
            skipped += 1
            details.append({
                "image_id": img.id, "filename": img.filename,
                "result": "skipped", "reason": "no annotation"
            })
            continue

        old_label_id = img.final_label_id
        old_ai_top1 = (img.ai_prediction or {}).get("top1") if had_ai else None

        # ---- 按 task_type 分派清理 ----

        # 1) 分类字段: 清 final_label_id + ai_prediction + status
        img.final_label_id = None
        img.annotated_by = None
        img.annotated_at = None
        if had_ai:
            img.ai_prediction = None
        img.status = "pending"

        # 2) 检测: 删 BBoxAnnotation 行
        if had_bbox:
            await db.execute(
                delete(BBoxAnnotation).where(BBoxAnnotation.image_id == img.id)
            )
            bbox_cleared_total += bbox_count

        # 3) 分割: 删 SegmentationMask 行 + 物理 PNG 文件
        if had_mask:
            # 先尝试删物理文件 (即使失败也不阻塞 SQL 清理, 只记录 warning)
            try:
                abs_path = Path(storage_service.base_dir) / mask_obj.mask_path
                if abs_path.is_file():
                    abs_path.unlink()
            except Exception as e:
                # 文件删除失败不阻塞主流程, 记录到 details 供排查
                details.append({
                    "image_id": img.id, "filename": img.filename,
                    "result": "warning",
                    "reason": f"物理 mask 文件删除失败: {e}"
                })
            await db.execute(
                delete(SegmentationMask).where(SegmentationMask.image_id == img.id)
            )
            mask_cleared_total += 1

        # 写审计 (仅分类场景, 检测/分割的审计由它们各自的 log 表承担, 此处保留向后兼容)
        if had_label or had_human or had_ai:
            log = AnnotationLog(
                image_id=img.id,
                user_id=current_user.id,
                action="reject",
                from_label_id=old_label_id,
                to_label_id=None,
                time_spent_ms=0,
            )
            db.add(log)

        # 聚合计数变化 (分类维度)
        if had_human or had_label:
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
            "task_type": img.task_type,
            "result": "cleared",
            "old_label_id": old_label_id,
            "had_ai": had_ai,
            "ai_cleared": had_ai,
            "old_ai_top1": old_ai_top1,
            # v2.5.16: 返回每张图实际清理量, 方便前端 / 审计
            "bbox_cleared": bbox_count,
            "mask_cleared": 1 if had_mask else 0,
        })

    # 2) 扣减 category.sample_count (下限 0) - 分类维度
    for cat_id, delta in sample_delta_by_cat.items():
        from sqlalchemy import case as sa_case
        new_cnt = sa_case(
            (Category.sample_count - delta < 0, 0),
            else_=Category.sample_count - delta,
        )
        await db.execute(
            update(Category).where(Category.id == cat_id).values(sample_count=new_cnt)
        )

    # 3) 扣减 dataset.annotated_count (下限 0) - 分类维度
    # 检测/分割的 annotated_count 维护在 detection.py/segmentation.py 各自的逻辑里
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
        # v2.5.16: 新增按 task_type 维度的清理计数
        "bbox_cleared_count": bbox_cleared_total,
        "mask_cleared_count": mask_cleared_total,
        "items": details,
    }


@router.get("/stats/{dataset_id}")
async def annotation_stats(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    标注效率统计 (产品运营核心指标数据源)
    - 各状态图片数
    - AI 节省时间估算
    - 标注员人均速度
    - v3.0.0: 不合格图片单独统计 (正交于 status 状态机), status_counts 排除不合格图
    """
    from sqlalchemy import func
    # 各状态图片数 (v3.0.0: 排除不合格图片, 不合格单独统计)
    stmt = (
        select(Image.status, func.count(Image.id))
        .where(
            Image.dataset_id == dataset_id,
            Image.quality_flag.is_(None),
        )
        .group_by(Image.status)
    )
    status_counts = dict((await db.execute(stmt)).all())

    # v3.0.0: 不合格图片数 (供前端顶部统计卡展示)
    unqualified_count = (await db.execute(
        select(func.count(Image.id)).where(
            Image.dataset_id == dataset_id,
            Image.quality_flag == "unqualified",
        )
    )).scalar() or 0

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
        "unqualified_count": unqualified_count,  # v3.0.0: 不合格图片数
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
    # P1-2: 非 admin 只能看自己 dataset 的标注日志
    from app.tasks.model.dataset import Dataset
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    if not current_user.is_admin() and ds.owner_id != current_user.id:
        raise HTTPException(403, "无权限查看此数据集的标注日志")

    from app.admin.model.user import User as UserModel
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

    # v3.1.0 Phase V #5: 批量预查 category name, 消除 N+1 (每条 log 走 2 次 db.get)
    # 之前: for log in rows: db.get(Category, from_label_id) + db.get(Category, to_label_id)
    # N 条 log → 2N 次 query. 现在: 1 次 batch query.
    cat_ids: set = set()
    for log, _fn, _u in rows:
        if log.from_label_id:
            cat_ids.add(log.from_label_id)
        if log.to_label_id:
            cat_ids.add(log.to_label_id)
    cat_name_map: dict[int, str] = {}
    if cat_ids:
        cat_rows = (await db.execute(
            select(Category.id, Category.name).where(Category.id.in_(cat_ids))
        )).all()
        cat_name_map = {r[0]: r[1] for r in cat_rows}

    items = []
    for log, filename, username in rows:
        from_lab = cat_name_map.get(log.from_label_id) if log.from_label_id else None
        to_lab = cat_name_map.get(log.to_label_id) if log.to_label_id else None
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
    from app.admin.model.user import User as UserModel
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
