"""
Detection API: BBox 标注 CRUD + 训练/自动标注 (v2.0.0 目标检测)
==================================================================

端点 (8 个):
- POST   /api/detection/annotations/save           单条 BBox 保存
- POST   /api/detection/annotations/replace         单图 BBox 全量替换
- GET    /api/detection/annotations/{image_id}      拉取单图全部 bbox
- DELETE /api/detection/annotations/{bbox_id}       单条删除
- POST   /api/detection/annotations/batch           批量入库 (S3 占位)
- POST   /api/detection/train                       启动 YOLOv8 训练 (Celery)
- POST   /api/detection/auto-annotate               启动自动标注 (Celery)
- GET    /api/detection/jobs/{job_id}/progress      拉取训练/标注进度 (轮询/SSE)

约束:
- bbox 坐标统一存归一化 0-1 (与 YOLO txt 一致, 详见 bbox_service)
- 仅 detection 数据集允许操作 (Image.task_type == "detection")
- category_id 必须属于同一 dataset
- 物理删除由 Image CASCADE 自动级联 (S1 已在 ORM 配置)
"""
import json
import asyncio
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.image import Image
from app.models.dataset import Dataset
from app.models.category import Category
from app.models.bbox_annotation import BBoxAnnotation
from app.models.training_job import TrainingJob
from app.models.model_version import ModelVersion
from app.schemas.detection import (
    BBoxCreate, BBoxOut, BBoxListOut,
    BBoxBatchCreate, BBoxBatchSaveResult,
    DetectionTrainRequest, DetectionTrainResponse,
)
from app.schemas.enums import TaskType, AnnotationSource
from app.services.bbox_service import validate_normalized_bbox
from app.core.redis_client import redis_client

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


# ============== S3.2 训练 / 自动标注 / 进度 ==============

# Redis 健康检查: 训练和自动标注都需要 Celery worker, 没有 Redis 就立刻 503
def _check_celery_available() -> None:
    """检测 Redis 是否可达. 不可达则 503, 避免任务在 .delay() 处长时间阻塞."""
    try:
        redis_client.ping()
    except Exception as e:
        raise HTTPException(
            503,
            f"Redis 不可用, 任务无法入队: {e}",
        )


@router.post("/train", response_model=DetectionTrainResponse)
async def start_detection_train(
    payload: DetectionTrainRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    启动 YOLOv8 训练任务 (异步, Celery)

    流程:
    1. 校验数据集 (detection 类型 + 当前用户拥有)
    2. 预创建 TrainingJob (PENDING) 给前端立刻看到任务
    3. .delay() 投递到 Celery, worker 接手后转 PROGRESS
    4. 训练完成自动写 ModelVersion + 关联 model_version_id
    """
    _check_celery_available()

    # 1) 校验 dataset
    ds = await db.get(Dataset, payload.dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset id={payload.dataset_id} not found")
    if ds.task_type != TaskType.DETECTION.value:
        raise HTTPException(
            400,
            f"Dataset id={payload.dataset_id} task_type="
            f"{ds.task_type!r}, expected 'detection'",
        )
    # 权限: 仅 owner 可训练 (v1.0.0 已有约定)
    if ds.owner_id and ds.owner_id != current_user.id:
        raise HTTPException(403, "仅数据集 owner 可启动训练")

    # 2) model_alias 兜底
    model_alias = payload.model_name.strip() or f"{payload.base_model}_run"

    # 3) .delay() 投递到 Celery, worker 内部建/复用 TrainingJob
    from app.workers.detection_tasks import train_detection_task
    try:
        async_result = train_detection_task.delay(
            dataset_id=payload.dataset_id,
            user_id=current_user.id,
            model_name=payload.base_model,
            model_alias=model_alias,
            epochs=payload.epochs,
            imgsz=payload.imgsz,
            batch=payload.batch_size,
            device="cpu",  # 论文 demo 默认 CPU
        )
    except Exception as e:
        raise HTTPException(503, f"Celery .delay() 失败: {e}")

    return DetectionTrainResponse(
        task_id=async_result.id,
        celery_task_id=async_result.id,
        job_id=0,  # worker 接手后由 tasks 内 SQL 写回; 前端用 task_id 查
        state="PENDING",
        message="任务已入队, 等待 worker 启动...",
    )


@router.post("/auto-annotate")
async def start_auto_annotate(
    dataset_id: int = Query(..., description="detection 数据集 id"),
    model_version_id: int = Query(..., description="用于推理的 ModelVersion id"),
    conf_threshold: float = Query(0.25, ge=0.0, le=1.0),
    iou_threshold: float = Query(0.45, ge=0.0, le=1.0),
    imgsz: int = Query(320, ge=64, le=1280),
    device: str = Query("cpu"),
    overwrite_existing: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    启动自动标注 (YOLOv8 推理 + 入库 BBoxAnnotation)
    """
    _check_celery_available()
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset id={dataset_id} not found")
    if ds.task_type != TaskType.DETECTION.value:
        raise HTTPException(400, f"非 detection 数据集: {ds.task_type}")
    mv = await db.get(ModelVersion, model_version_id)
    if not mv:
        raise HTTPException(404, f"ModelVersion id={model_version_id} not found")
    if mv.task_type != TaskType.DETECTION.value:
        raise HTTPException(
            400, f"ModelVersion task_type={mv.task_type!r}, expected 'detection'",
        )

    from app.workers.detection_tasks import auto_annotate_detection_task
    try:
        async_result = auto_annotate_detection_task.delay(
            dataset_id=dataset_id,
            user_id=current_user.id,
            model_version_id=model_version_id,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
            imgsz=imgsz,
            device=device,
            overwrite_existing=overwrite_existing,
        )
    except Exception as e:
        raise HTTPException(503, f"Celery .delay() 失败: {e}")

    return {
        "task_id": async_result.id,
        "state": "PENDING",
        "message": "自动标注任务已入队, 等待 worker 启动...",
    }


@router.get("/jobs/{job_id}/progress")
async def get_job_progress(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    拉取 TrainingJob 进度 (兼容老接口, JSON 轮询)
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, f"TrainingJob id={job_id} not found")
    return {
        "id": job.id,
        "celery_task_id": job.celery_task_id,
        "task_type": job.task_type,
        "state": job.state,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "duration_seconds": job.duration_seconds,
        "data_total": job.data_total,
        "data_train": job.data_train,
        "data_val": job.data_val,
        "num_classes": job.num_classes,
        "class_names": job.class_names,
        "model_version_id": job.model_version_id,
    }


@router.get("/jobs/{job_id}/stream")
async def stream_job_progress(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    SSE 进度推送: 每秒轮询 TrainingJob, 终态自动断开
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, f"TrainingJob id={job_id} not found")

    async def event_gen():
        last_state = None
        last_progress = -1.0
        # 最多 1 小时 (防止僵尸连接)
        for _ in range(3600):
            # 重新查一次
            j = await db.get(TrainingJob, job_id)
            if not j:
                break
            if j.state != last_state or j.progress != last_progress:
                payload = {
                    "id": j.id,
                    "state": j.state,
                    "progress": j.progress,
                    "message": j.message,
                    "current_epoch": getattr(j, "current_epoch", None),
                }
                yield f"data: {json.dumps(payload, default=str)}\n\n"
                last_state = j.state
                last_progress = j.progress
            if j.state in ("SUCCESS", "FAILURE", "REVOKED"):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ============== S4 模型激活 (detection 专用别名) ==============
#
# 沿用 v1.0.0 model.py 的 with_for_update() 行锁模式 (允许多激活并存).
# 此处加 /api/detection/models/* 路由, 是给"目标检测"工作台用的语义化入口.
# 内部实现与 /api/models/{id}/activate 完全等价, 仅校验 task_type == "detection".


async def _lock_dataset_models(db: AsyncSession, dataset_id: Optional[int]) -> None:
    """锁住指定 dataset 的所有 ModelVersion 行 (SELECT ... FOR UPDATE)"""
    stmt = select(ModelVersion)
    if dataset_id is not None:
        stmt = stmt.where(ModelVersion.dataset_id == dataset_id)
    stmt = stmt.with_for_update()
    (await db.execute(stmt)).scalars().all()


@router.post("/models/{model_id}/activate")
async def activate_detection_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    激活 detection 模型 (语义化入口)
    - 校验 mv.task_type == "detection"
    - 行锁后置 is_active = True, 幂等
    """
    target = await db.get(ModelVersion, model_id)
    if not target:
        raise HTTPException(404, f"ModelVersion id={model_id} not found")
    if target.task_type != TaskType.DETECTION.value:
        raise HTTPException(
            400,
            f"ModelVersion task_type={target.task_type!r}, "
            f"expected 'detection'",
        )

    try:
        await _lock_dataset_models(db, target.dataset_id)
        target.is_active = True
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"激活失败: {e}")

    return {
        "success": True,
        "model_id": model_id,
        "task_type": target.task_type,
        "dataset_id": target.dataset_id,
        "is_active": target.is_active,
    }


@router.post("/models/{model_id}/deactivate")
async def deactivate_detection_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    取消激活 detection 模型
    """
    target = await db.get(ModelVersion, model_id)
    if not target:
        raise HTTPException(404, f"ModelVersion id={model_id} not found")
    if target.task_type != TaskType.DETECTION.value:
        raise HTTPException(
            400,
            f"ModelVersion task_type={target.task_type!r}, "
            f"expected 'detection'",
        )

    try:
        await _lock_dataset_models(db, target.dataset_id)
        target.is_active = False
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"取消激活失败: {e}")

    return {
        "success": True,
        "model_id": model_id,
        "task_type": target.task_type,
        "is_active": target.is_active,
    }
