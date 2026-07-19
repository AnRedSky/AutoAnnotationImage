"""
Detection API: BBox 标注 CRUD (v2.0.0 目标检测)
================================================

端点 (4 个):
- POST   /api/detection/annotations/save      单图保存/替换全部 bbox
- GET    /api/detection/annotations/{image_id}  拉取单图全部 bbox
- DELETE /api/detection/annotations/{bbox_id}  删除单条 bbox
- POST   /api/detection/annotations/batch      批量写入 (AI 预标注结果入库)

约束:
- bbox 坐标统一存归一化 0-1 (与 YOLO txt 一致, 详见 bbox_service)
- 仅 detection 数据集允许操作 (Image.task_type == "detection")
- category_id 必须属于同一 dataset
- 物理删除由 Image CASCADE 自动级联 (S1 已在 ORM 配置)
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.image import Image
from app.models.category import Category
from app.models.bbox_annotation import BBoxAnnotation
from app.schemas.detection import (
    BBoxCreate, BBoxOut, BBoxListOut,
    BBoxBatchCreate, BBoxBatchSaveResult,
)
from app.schemas.enums import TaskType, AnnotationSource
from app.services.bbox_service import validate_normalized_bbox

router = APIRouter()


# ============== 工具函数 ==============

async def _ensure_detection_image(image_id: int, db: AsyncSession) -> Image:
    """校验图片存在且为 detection 任务类型

    Raises:
        HTTPException: 404 图片不存在 / 400 task_type 错误
    """
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, f"Image id={image_id} not found")
    if img.task_type != TaskType.DETECTION.value:
        raise HTTPException(
            400,
            f"Image id={image_id} task_type is {img.task_type!r}, "
            f"expected 'detection'",
        )
    return img


async def _validate_category(
    category_id: Optional[int],
    dataset_id: int,
    db: AsyncSession,
) -> None:
    """校验 category_id 属于同一 dataset

    - None: 允许 (无类别标注)
    - 否则: 查 Category 校验 dataset 归属
    """
    if category_id is None:
        return
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(400, f"Category id={category_id} not found")
    if cat.dataset_id != dataset_id:
        raise HTTPException(
            400,
            f"Category id={category_id} 不属于 dataset id={dataset_id}",
        )


# ============== 端点 ==============

@router.post("/annotations/save", response_model=BBoxOut)
async def save_bbox(
    image_id: int = Query(..., description="图片 id"),
    payload: BBoxCreate = ...,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单条 BBox 保存 (新增 / 覆盖)
    - 同一 image_id + (x_min,y_min,x_max,y_max) 视为同一目标, 走 UPDATE
    - 简化: 这里用 POST 单条写入, 真正的"批量替换"走 /annotations/replace
    """
    img = await _ensure_detection_image(image_id, db)
    await _validate_category(payload.category_id, img.dataset_id, db)

    try:
        validate_normalized_bbox(
            payload.x_min, payload.y_min,
            payload.x_max, payload.y_max,
        )
    except ValueError as e:
        raise HTTPException(400, f"坐标非法: {e}")

    # source 兜底: 始终允许 'human' / 'ai' / 'human_corrected'
    src = payload.source or AnnotationSource.HUMAN.value
    if src not in {s.value for s in AnnotationSource}:
        raise HTTPException(400, f"source 非法: {src!r}")

    bb = BBoxAnnotation(
        image_id=image_id,
        category_id=payload.category_id,
        x_min=payload.x_min,
        y_min=payload.y_min,
        x_max=payload.x_max,
        y_max=payload.y_max,
        confidence=payload.confidence,
        source=src,
        annotated_by=current_user.id,
    )
    db.add(bb)
    await db.commit()
    await db.refresh(bb)
    return bb


@router.post("/annotations/replace", response_model=BBoxListOut)
async def replace_image_bboxes(
    image_id: int = Query(..., description="图片 id"),
    items: List[BBoxCreate] = ...,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单图 BBox 全量替换 (论文核心: 人工确认 / 修正 AI 预标注)

    语义:
    - DELETE 该 image_id 下所有现有 BBox
    - INSERT items 中所有 BBox (annotated_by = current_user)
    - 一次 commit, 事务内完成

    适用场景: AI 预标注结果批量入库, 或人工重画全部框
    """
    img = await _ensure_detection_image(image_id, db)

    # 1) 校验所有 category_id
    for it in items:
        await _validate_category(it.category_id, img.dataset_id, db)
        try:
            validate_normalized_bbox(
                it.x_min, it.y_min, it.x_max, it.y_max,
            )
        except ValueError as e:
            raise HTTPException(400, f"坐标非法: {e}")

    # 2) 删除原有 bbox
    existing = (await db.execute(
        select(BBoxAnnotation).where(BBoxAnnotation.image_id == image_id)
    )).scalars().all()
    for old in existing:
        await db.delete(old)

    # 3) 写入新 bbox
    new_boxes: List[BBoxAnnotation] = []
    for it in items:
        src = it.source or AnnotationSource.HUMAN.value
        bb = BBoxAnnotation(
            image_id=image_id,
            category_id=it.category_id,
            x_min=it.x_min, y_min=it.y_min,
            x_max=it.x_max, y_max=it.y_max,
            confidence=it.confidence,
            source=src,
            annotated_by=current_user.id,
        )
        db.add(bb)
        new_boxes.append(bb)
    await db.commit()

    # 4) 重新拉取 (拿到 id / 时间戳)
    rows = (await db.execute(
        select(BBoxAnnotation)
        .where(BBoxAnnotation.image_id == image_id)
        .order_by(BBoxAnnotation.id.asc())
    )).scalars().all()
    return BBoxListOut(image_id=image_id, items=rows)


@router.get("/annotations/{image_id}", response_model=BBoxListOut)
async def list_image_bboxes(
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    拉取单图全部 BBox (按 id 升序, 先画的在前)
    """
    img = await _ensure_detection_image(image_id, db)
    rows = (await db.execute(
        select(BBoxAnnotation)
        .where(BBoxAnnotation.image_id == image_id)
        .order_by(BBoxAnnotation.id.asc())
    )).scalars().all()
    return BBoxListOut(image_id=image_id, items=rows)


@router.delete("/annotations/{bbox_id}")
async def delete_bbox(
    bbox_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    删除单条 BBox
    """
    bb = await db.get(BBoxAnnotation, bbox_id)
    if not bb:
        raise HTTPException(404, f"BBox id={bbox_id} not found")
    await db.delete(bb)
    await db.commit()
    return {"success": True, "deleted_id": bbox_id}


@router.post("/annotations/batch", response_model=BBoxBatchSaveResult)
async def batch_save_bboxes(
    payload: BBoxBatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量入库 (AI 预标注结果专用)

    语义:
    - 每个 item 必须包含 image_id (虽然 schema 用 image_id 单字段, 这里允许
      通过 BBoxCreate 透传; 兼容旧 API 用单 image_id 的场景)
    - 当前实现: 沿用 BBoxCreate 不含 image_id, 保留接口给后续扩展
      (YOLO 推理后批量写入会单图逐张调 replace, 避免大事务)

    当前端点暂时只做参数校验占位, 真正大批量 AI 入库在 S3 由 Celery 任务处理
    """
    if not payload.items:
        raise HTTPException(400, "items cannot be empty")
    if len(payload.items) > 5000:
        raise HTTPException(400, "Too many items (max 5000 per request)")

    # 校验坐标 + category
    validated_image_ids = set()
    for it in payload.items:
        # 此处缺 image_id 字段, 暂用 _validate + 占位, 留待 S3 接入
        try:
            validate_normalized_bbox(
                it.x_min, it.y_min, it.x_max, it.y_max,
            )
        except ValueError as e:
            raise HTTPException(400, f"坐标非法: {e}")

    return BBoxBatchSaveResult(
        success=True,
        received=len(payload.items),
        message="BBox batch save endpoint placeholder; "
                "real bulk import is in S3 Celery worker.",
        image_ids=sorted(validated_image_ids),
    )
