"""
image.query 模块 — 图片查询 API
================================

v3.0.0 Phase N 拆分: 从 image.py 抽离
- 职责: 列表查询 (分页) + 单图详情
- 包含 v2.5.17 修复: 批量补齐 detection/segmentation 的"实际标注数"
  (bbox_count / has_mask 字段供前端「去标」按钮使用)
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.tasks.model.annotation_log import AnnotationLog
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user

# query 独立 router
router = APIRouter()


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
                "task_type": img.task_type,
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
        "task_type": img.task_type,
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
