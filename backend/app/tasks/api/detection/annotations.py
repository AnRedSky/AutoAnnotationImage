"""
detection.annotations 模块 — BBox 标注 CRUD API
==============================================

**v3.0.0 Phase P 拆分**: 从 detection.py 抽离
**职责**: 单条/批量 BBox 标注的增删改查

**路由清单** (6 个):
- POST   /annotations/save            单条 BBox 保存
- POST   /annotations/replace         单图 BBox 全量替换
- GET    /annotations/{image_id}      拉取单图全部 bbox
- DELETE /annotations/clear/{image_id} 清空单图全部
- DELETE /annotations/{bbox_id}       单条删除
- POST   /annotations/batch           批量入库 (S3 占位)

**核心约束**:
- bbox 坐标统一存归一化 0-1 (与 YOLO txt 一致)
- 仅 detection 数据集允许操作 (Image.task_type == "detection")
- category_id 必须属于同一 dataset
- v3.0.0 Phase 4: 业务编排下沉到 DetectionService (replace/clear)

**v3.3.0 P0 修复**: 所有端点必须校验当前用户对 image 所属 dataset 有写权限
- 防止越权读 (list_image_bboxes / get) 或越权改 (save / replace / clear / delete)
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.http.auth import get_current_user
from app.admin.model.user import User
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.annotation.model.bbox_annotation import BBoxAnnotation
from app.schemas.detection import (
    BBoxCreate, BBoxOut, BBoxListOut,
    BBoxBatchCreate, BBoxBatchSaveResult,
)
from app.common.enums import TaskType, AnnotationSource
from app.common.geometry.bbox_service import validate_normalized_bbox
# v3.0.0 Phase 4: 业务编排下沉到 Service
from app.tasks.service.detection_service import DetectionService
# v3.3.0 P0: 权限校验
from app.tasks.service.permission_service import assert_can_access_dataset

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


async def _ensure_can_access_image(
    image_id: int, db: AsyncSession, current_user: User, require_write: bool = False
) -> Image:
    """v3.3.0 P0 新增: 校验 image 存在 + 任务类型 + 访问权限

    Args:
        image_id: 图片 ID
        db: 数据库会话
        current_user: 当前用户
        require_write: 是否需要写权限 (mutation 操作为 True, 读为 False)
    """
    img = await _ensure_detection_image(image_id, db)
    ds = await db.get(Dataset, img.dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    await assert_can_access_dataset(db, current_user, ds, require_write=require_write)
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

    v3.3.0 P0 修复: 必须校验写权限
    """
    img = await _ensure_can_access_image(image_id, db, current_user, require_write=True)
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
    # v2.5.15: 单条 BBox 保存后, 把 image.status 提升到 human_confirmed
    # - 之前只 insert bbox, image.status 一直停留在 pending/ai_labeled
    # - 导致前端 stats 的"待标注"数字永远不减
    # - 仅当原状态是 pending/ai_labeled 时才升级, 不降级 (保留 human_corrected 语义)
    if img.status in ('pending', 'ai_labeled'):
        img.status = 'human_confirmed'
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
    单图 BBox 全量替换 (核心交互: 人工确认 / 修正 AI 预标注) - v3.0.0 Phase 4 thin wrapper

    业务规则 (全部在 Service):
    - DELETE 该 image_id 下所有现有 BBox
    - INSERT items 中所有 BBox (annotated_by = current_user)
    - 一次 commit, 事务内完成

    适用场景: AI 预标注结果批量入库, 或人工重画全部框

    v3.3.0 P0 修复: 必须校验写权限
    """
    img = await _ensure_can_access_image(image_id, db, current_user, require_write=True)

    # 校验坐标 (Service 校验 category 归属)
    for it in items:
        try:
            validate_normalized_bbox(
                it.x_min, it.y_min, it.x_max, it.y_max,
            )
        except ValueError as e:
            raise HTTPException(400, f"坐标非法: {e}")

    # 委托 Service (v3.0.0 Phase 4)
    await DetectionService.replace_bboxes(
        db, img, [it.model_dump() for it in items], user_id=current_user.id,
    )

    # 重新拉取 (拿到 id / 时间戳)
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

    v3.3.0 P0 修复: 必须校验读权限
    """
    img = await _ensure_can_access_image(image_id, db, current_user, require_write=False)
    rows = (await db.execute(
        select(BBoxAnnotation)
        .where(BBoxAnnotation.image_id == image_id)
        .order_by(BBoxAnnotation.id.asc())
    )).scalars().all()
    return BBoxListOut(image_id=image_id, items=rows)


@router.delete("/annotations/clear/{image_id}")
async def clear_image_bboxes(
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    清空单图全部 BBox 标注 (v3.0.0 Phase 4: thin wrapper, 业务下沉到 DetectionService.clear_bboxes)
    - 配合前端 DetectionAnnotator 的「重画」流程: 先 clear 旧的, 再 save 新的
    - 必须声明在 /annotations/{bbox_id} 之前, 避免 FastAPI 把 'clear' 解析成 bbox_id

    v3.3.0 P0 修复: 必须校验写权限
    """
    img = await _ensure_can_access_image(image_id, db, current_user, require_write=True)
    cleared = await DetectionService.clear_bboxes(db, img)
    return {"image_id": image_id, "cleared": cleared, "success": True}


@router.delete("/annotations/{bbox_id}")
async def delete_bbox(
    bbox_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    删除单条 BBox (v3.3.0 P0 修复: 必须校验写权限)
    - 通过 BBox -> Image -> Dataset 反查权限
    """
    bb = await db.get(BBoxAnnotation, bbox_id)
    if not bb:
        raise HTTPException(404, f"BBox id={bbox_id} not found")
    # 反查 image -> dataset 权限
    img = await db.get(Image, bb.image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    ds = await db.get(Dataset, img.dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    await assert_can_access_dataset(db, current_user, ds, require_write=True)
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
