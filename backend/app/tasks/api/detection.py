"""
Detection API: BBox 标注 CRUD + 训练/自动标注 (v2.0.0 目标检测)
==================================================================

端点 (10 个):
- POST   /api/detection/annotations/save             单条 BBox 保存
- POST   /api/detection/annotations/replace         单图 BBox 全量替换
- GET    /api/detection/annotations/{image_id}      拉取单图全部 bbox
- DELETE /api/detection/annotations/{bbox_id}       单条删除
- POST   /api/detection/annotations/batch           批量入库 (S3 占位)
- POST   /api/detection/train                       启动 YOLOv8 训练 (Celery)
- POST   /api/detection/auto-annotate               启动自动标注 (Celery)
- GET    /api/detection/jobs/{job_id}/progress      拉取 TrainingJob 进度 (老接口, int job_id)
- GET    /api/detection/jobs/{job_id}/stream        SSE 推 TrainingJob 进度 (老接口)
- GET    /api/detection/progress/{task_id}          REST 轮询 (v2.5.35 新增, task_id = Celery UUID)
- GET    /api/detection/progress/stream/{task_id}   SSE 推送 (v2.5.35 新增, 与前端 detectionApi 对齐)
- GET    /api/detection/models/{id}/activate        激活 detection 模型 (单激活不变量)

约束:
- bbox 坐标统一存归一化 0-1 (与 YOLO txt 一致, 详见 bbox_service)
- 仅 detection 数据集允许操作 (Image.task_type == "detection")
- category_id 必须属于同一 dataset
- 物理删除由 Image CASCADE 自动级联 (S1 已在 ORM 配置)

v2.5.35 关键修复:
- 前端 detectionApi.progress 调 /api/detection/progress/{taskId} (Celery UUID),
  但后端原本只有 /api/detection/jobs/{job_id}/progress (int TrainingJob id),
  导致前端轮询永远 404. 新增与前端路径对齐的端点.
- auto_annotate_detection_task 不写 TrainingJob, 因此 progress 端点
  必须支持"DB 没记录就回退到 Celery result.info"路径.
- 强制单激活不变量: 同 dataset 激活时, 把其他 is_active=True 置 False.

v3.0.0 Phase 4 重构:
- replace_image_bboxes: 业务下沉到 DetectionService.replace_bboxes (-32 行)
- clear_image_bboxes: 业务下沉到 DetectionService.clear_bboxes (-2 行)
"""
import json
import asyncio
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.middleware.http.auth import get_current_user, get_user_optional_for_query
from app.utils.async_helpers import check_celery_available as _check_celery_available
from app.admin.model.user import User
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.annotation.model.bbox_annotation import BBoxAnnotation
from app.tasks.model.training_job import TrainingJob
from app.tasks.model.model_version import ModelVersion
from app.schemas.detection import (
    BBoxCreate, BBoxOut, BBoxListOut,
    BBoxBatchCreate, BBoxBatchSaveResult,
    DetectionTrainRequest, DetectionTrainResponse,
)
from app.schemas.enums import TaskType, AnnotationSource
from app.common.geometry.bbox_service import validate_normalized_bbox
# v3.0.0 Phase 4: 业务编排下沉到 Service
from app.tasks.service.detection_service import DetectionService

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
    """
    img = await _ensure_detection_image(image_id, db)

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
    """
    img = await _ensure_detection_image(image_id, db)
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
    """
    img = await _ensure_detection_image(image_id, db)
    cleared = await DetectionService.clear_bboxes(db, img)
    return {"image_id": image_id, "cleared": cleared, "success": True}


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
# Redis 健康检查统一使用 app.utils.async_helpers.check_celery_available


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
    from app.tasks.workers.detection import train_detection_task
    try:
        async_result = train_detection_task.delay(
            dataset_id=payload.dataset_id,
            user_id=current_user.id,
            model_name=payload.base_model,
            model_alias=model_alias,
            epochs=payload.epochs,
            imgsz=payload.imgsz,
            batch=payload.batch_size,
            device="cpu",  # 缺省 CPU, 适合开发/演示部署
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

    from app.tasks.workers.detection import auto_annotate_detection_task
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
        "model_name": mv.name,
        "model_version_id": mv.id,
        "base_model": mv.base_model,
        "message": (
            f"Fine-tune 自动标注已入队, 等待 worker 启动... "
            f"(model: {mv.name} · base: {mv.base_model})"
        ),
    }


# ============== v2.3.2: 用 ultralytics 预训练 yolov8n/s/m/l/x 做自动标注 (无需 ModelVersion) ==============
@router.post("/auto-annotate-pretrained")
async def start_auto_annotate_pretrained(
    dataset_id: int = Query(..., description="detection 数据集 id"),
    model_name: str = Query(
        "yolov8n",
        pattern="^yolov8[nsmxl]$",
        description="预训练 YOLO 模型名: yolov8n/s/m/l/x",
    ),
    conf_threshold: float = Query(0.25, ge=0.0, le=1.0),
    iou_threshold: float = Query(0.45, ge=0.0, le=1.0),
    imgsz: int = Query(640, ge=64, le=1280),
    device: str = Query("cpu"),
    overwrite_existing: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    启动预训练 YOLO 自动标注 (不依赖已训练 ModelVersion).
    ultralytics 会自动下载 yolov8n.pt 等权重, 默认从 COCO 80 类,
    与项目类目交集的部分会写 BBoxAnnotation, 不交集的会跳过.
    """
    _check_celery_available()
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset id={dataset_id} not found")
    if ds.task_type != TaskType.DETECTION.value:
        raise HTTPException(400, f"非 detection 数据集: {ds.task_type}")

    # 校验数据集类目与 COCO 80 类的交集, 提示用户可能无命中
    coco_class_names = _get_coco_class_names()
    async def _load_cats() -> list:
        from app.tasks.model.category import Category
        from sqlalchemy import select as _sel
        rows = (await db.execute(
            _sel(Category).where(Category.dataset_id == dataset_id)
        )).scalars().all()
        return [c.name for c in rows]
    cat_names = await _load_cats()
    matched = [n for n in cat_names if n.lower() in coco_class_names]
    # v2.5.32: 同时返回未匹配的项目类目, 让前端能提示用户具体哪些
    # 类目名不与 COCO 80 类重合 (避免「未匹配任何 COCO 类」这种宽泛提示)
    unmatched = [n for n in cat_names if n.lower() not in coco_class_names]
    if not matched and cat_names:
        # 不阻塞, 仅 warning
        pass

    from app.tasks.workers.detection import auto_annotate_pretrained_task
    try:
        async_result = auto_annotate_pretrained_task.delay(
            dataset_id=dataset_id,
            user_id=current_user.id,
            model_name=model_name,
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
        "model_name": model_name,
        "matched_coco_classes": matched,
        # v2.5.32: 前端提示用, 让用户知道自己的类目为什么没匹配
        "dataset_categories": cat_names,
        "unmatched_categories": unmatched,
        "message": (
            f"预训练 {model_name} 自动标注已入队, 等待 worker..."
            f" 与 COCO 80 类匹配 {len(matched)}/{len(cat_names)} 个项目类目"
        ),
    }


def _get_coco_class_names() -> set:
    """返回 ultralytics YOLOv8 默认 80 类的 set (lowercase)."""
    try:
        from ultralytics import YOLO
        # 加载 yolov8n 拿 names, 第一次会下载很慢, 失败回退到内置列表
        m = YOLO("yolov8n.pt")
        return {n.lower() for n in m.names.values()}
    except Exception:
        # 内置 80 类 (COCO 2017) 备用
        return {
            "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat",
            "traffic light","fire hydrant","stop sign","parking meter","bench","bird","cat",
            "dog","horse","sheep","cow","elephant","bear","zebra","giraffe","backpack","umbrella",
            "handbag","tie","suitcase","frisbee","skis","snowboard","sports ball","kite",
            "baseball bat","baseball glove","skateboard","surfboard","tennis racket","bottle",
            "wine glass","cup","fork","knife","spoon","bowl","banana","apple","sandwich",
            "orange","broccoli","carrot","hot dog","pizza","donut","cake","chair","couch",
            "potted plant","bed","dining table","toilet","tv","laptop","mouse","remote",
            "keyboard","cell phone","microwave","oven","toaster","sink","refrigerator","book",
            "clock","vase","scissors","teddy bear","hair drier","toothbrush",
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


# ============== v2.5.35 新增: 与前端 detectionApi 路径对齐的进度端点 ==============
# 前端 detectionApi.progress 调 GET /api/detection/progress/{taskId} (Celery UUID),
# detectionApi.streamProgress 调 GET /api/detection/progress/stream/{taskId}.
# 老的 /jobs/{job_id}/progress 端点保留, 但前端轮询已经走新端点.

async def _resolve_detection_task_progress(task_id: str) -> dict:
    """统一解析 Celery task_id -> 进度 dict, 同时覆盖训练 + 自动标注两种来源.

    优先级:
    1. TrainingJob 表 (训练任务走的是 _create_job 路径, celery_task_id 是主键索引列)
    2. Celery AsyncResult (auto_annotate_detection_task 不写 TrainingJob, 只走 update_state)

    返回字段:
      { task_id, state, progress, message, source: 'db'|'celery'|'unknown',
        total (auto_annotate 终态下的 total/auto_labeled/no_match), result (Celery 终态返回值) }
    """
    from celery.result import AsyncResult
    from app.database import AsyncSessionLocal

    db_row = None
    try:
        async with AsyncSessionLocal() as db:
            db_row = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
    except Exception:
        db_row = None

    # 拉 Celery result (auto_annotate 也走这里)
    celery_state = "PENDING"
    celery_info: dict = {}
    celery_result_payload = None
    try:
        result = AsyncResult(task_id)
        try:
            celery_state = result.state
        except Exception:
            celery_state = "PENDING"
        try:
            raw = result.info
            if isinstance(raw, dict):
                celery_info = raw
        except Exception:
            celery_info = {}
        # 终态返回值 (SUCCESS 时 result.info 是 task return dict, FAILURE 时是 exc_info)
        try:
            if celery_state == "SUCCESS":
                celery_result_payload = result.result
        except Exception:
            celery_result_payload = None
    except Exception:
        pass

    if db_row is not None:
        # DB 有记录 (训练任务), 用 DB 状态做权威
        state = db_row.state or celery_state
        progress = float(db_row.progress) if db_row.progress is not None else float(celery_info.get("progress", 0))
        message = db_row.message or celery_info.get("msg") or celery_info.get("info") or ""
        if db_row.error and not message:
            message = db_row.error[:200]
        return {
            "task_id": task_id,
            "state": state,
            "progress": round(progress, 2),
            "message": message,
            "current_epoch": getattr(db_row, "current_epoch", None),
            "total_epochs": db_row.epochs,
            "started_at": db_row.started_at.isoformat() if db_row.started_at else None,
            "finished_at": db_row.finished_at.isoformat() if db_row.finished_at else None,
            "duration_seconds": db_row.duration_seconds,
            "data_total": db_row.data_total,
            "data_train": db_row.data_train,
            "data_val": db_row.data_val,
            "num_classes": db_row.num_classes,
            "class_names": db_row.class_names,
            "model_version_id": db_row.model_version_id,
            "job_id": db_row.id,
            "source": "db",
        }

    # DB 没记录 (auto_annotate 任务), 用 Celery state + info
    return {
        "task_id": task_id,
        "state": celery_state,
        "progress": round(float(celery_info.get("progress", 0)), 2),
        "message": celery_info.get("msg") or celery_info.get("info") or "",
        "current_epoch": celery_info.get("current_epoch") or celery_info.get("epoch"),
        "total_epochs": celery_info.get("total_epochs"),
        # auto_annotate 终态返回 {status, total, auto_labeled, no_match}
        "total": celery_info.get("total") or (celery_result_payload.get("total") if isinstance(celery_result_payload, dict) else None),
        "auto_labeled": celery_info.get("auto_labeled") or (celery_result_payload.get("auto_labeled") if isinstance(celery_result_payload, dict) else None),
        "no_match": celery_info.get("no_match") or (celery_result_payload.get("no_match") if isinstance(celery_result_payload, dict) else None),
        "result": celery_result_payload if isinstance(celery_result_payload, dict) else None,
        "source": "celery" if celery_state != "PENDING" else "unknown",
    }


@router.get("/progress/{task_id}")
async def get_detection_progress(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    拉取 detection 任务进度 (REST 轮询)

    v2.5.35 新增: 与前端 detectionApi.progress 路径对齐
    - task_id 是 Celery UUID (不是 int TrainingJob id)
    - 同时覆盖 train_detection_task (DB 有 TrainingJob) 和
      auto_annotate_detection_task / auto_annotate_pretrained_task (仅 Celery state)
    """
    return await _resolve_detection_task_progress(task_id)


@router.get("/progress/stream/{task_id}")
async def stream_detection_progress(
    task_id: str,
    request: Request,
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_user_optional_for_query),
):
    """
    SSE 实时推送 detection 任务进度 (v2.5.35 新增, 与前端 detectionApi.streamProgress 对齐)

    - 每 1s 拉一次 _resolve_detection_task_progress
    - 仅在 (state, progress, message) 签名变化时推送, 避免静默期洪水
    - 终态推完最后一帧后服务端主动结束流
    - 客户端断开通过 request.is_disconnected() 立即退出
    - 鉴权: query ?token=xxx 优先 (EventSource 无法设 header), header 兜底
    """
    if current_user is None:
        raise HTTPException(401, "未授权: 需要有效的 access_token (query ?token= 或 Authorization header)")

    SSE_DET_POLL_INTERVAL = 1.0

    async def event_gen():
        last_signature: Optional[tuple] = None
        # 首帧
        try:
            yield f": connected task_id={task_id}\n\n"
        except Exception:
            return

        while True:
            # 客户端断开检测
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break

            try:
                data = await _resolve_detection_task_progress(task_id)
            except Exception as e:
                # 出错也不中断流, 给前端一个可读的 error 帧
                data = {
                    "task_id": task_id,
                    "state": "FAILURE",
                    "progress": 0.0,
                    "message": f"进度查询失败: {type(e).__name__}: {str(e)[:200]}",
                }

            # 仅透传关键字段, 减少 payload
            payload = {
                "task_id": data["task_id"],
                "state": data["state"],
                "progress": data["progress"],
                "message": data["message"],
            }
            # 透传 auto_annotate 终态统计
            for k in ("total", "auto_labeled", "no_match",
                      "current_epoch", "total_epochs",
                      "data_total", "data_train", "data_val",
                      "num_classes", "class_names", "model_version_id",
                      "started_at", "finished_at", "duration_seconds", "source"):
                v = data.get(k)
                if v is not None:
                    payload[k] = v
            # 透传 Celery 终态返回值 (SUCCESS 时是 task return dict)
            if data.get("result") and isinstance(data["result"], dict):
                for k in ("total", "auto_labeled", "no_match", "status"):
                    if k in data["result"]:
                        payload.setdefault(k, data["result"][k])

            signature = (payload["state"], round(payload["progress"], 1), payload["message"])
            if signature != last_signature:
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                last_signature = signature

            # 终态结束
            if payload["state"] in ("SUCCESS", "FAILURE", "REVOKED"):
                try:
                    yield "event: end\ndata: {}\n\n"
                except Exception:
                    pass
                break

            await asyncio.sleep(SSE_DET_POLL_INTERVAL)

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
    - v2.5.35: 单激活不变量 — 同 dataset 其他 active 全部置 False
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
        # v2.5.35: 强制单激活 — 同 dataset 其他 active 全部置 False
        siblings = (await db.execute(
            select(ModelVersion).where(
                ModelVersion.dataset_id == target.dataset_id,
                ModelVersion.is_active == True,  # noqa: E712
                ModelVersion.id != target.id,
            )
        )).scalars().all()
        for sib in siblings:
            sib.is_active = False
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
        "deactivated_siblings": [s.id for s in siblings],
    }


# ============== 跨图复制建议 (v2.2.0 S9.3) ==============

@router.get("/copy-suggestion/{image_id}")
async def copy_suggestion(
    image_id: int,
    min_source_count: int = Query(2, ge=1, description="至少几张图有同类别 bbox 才算建议"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    跨图 bbox 复制建议: 目标图的 task_type='detection', 返回按 category_id 分组的平均 bbox.
    实现:
      1) 查目标图所在 dataset
      2) 拉该 dataset 中已确认/已修正 (status in human_confirmed/human_corrected) 的其他图上
         同 dataset 的所有 BBoxAnnotation
      3) 按 category_id 分组, 计算 avg(x_min, y_min, x_max, y_max)
      4) 仅返回 source_count >= min_source_count 的类别 (避免噪声)
    返回: [{category_id, avg_x_min, avg_y_min, avg_x_max, avg_y_max, source_count}]
    """
    target_img = await db.get(Image, image_id)
    if not target_img:
        raise HTTPException(404, f"Image id={image_id} not found")
    if target_img.task_type != TaskType.DETECTION.value:
        raise HTTPException(
            400,
            f"Image task_type={target_img.task_type!r}, expected 'detection'",
        )

    # 同 dataset 的所有已确认/已修正图
    confirmed_rows = (await db.execute(
        select(BBoxAnnotation)
        .join(Image, BBoxAnnotation.image_id == Image.id)
        .where(
            Image.dataset_id == target_img.dataset_id,
            Image.task_type == TaskType.DETECTION.value,
            Image.id != image_id,  # 排除目标图自身
            Image.status.in_(["human_confirmed", "human_corrected"]),
        )
    )).scalars().all()

    # 按 category_id 分组
    from collections import defaultdict
    bucket: dict = defaultdict(list)
    for b in confirmed_rows:
        if b.category_id is None:
            continue
        bucket[b.category_id].append(b)

    suggestions = []
    for cat_id, items in bucket.items():
        if len(items) < min_source_count:
            continue
        n = len(items)
        avg_x_min = sum(b.x_min for b in items) / n
        avg_y_min = sum(b.y_min for b in items) / n
        avg_x_max = sum(b.x_max for b in items) / n
        avg_y_max = sum(b.y_max for b in items) / n
        suggestions.append({
            "category_id": cat_id,
            "avg_x_min": round(avg_x_min, 4),
            "avg_y_min": round(avg_y_min, 4),
            "avg_x_max": round(avg_x_max, 4),
            "avg_y_max": round(avg_y_max, 4),
            "source_count": n,
        })

    # 按 source_count desc 排序
    suggestions.sort(key=lambda x: x["source_count"], reverse=True)

    return {
        "image_id": image_id,
        "dataset_id": target_img.dataset_id,
        "suggestions": suggestions,
        "total_source_images": len({b.image_id for b in confirmed_rows}),
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
