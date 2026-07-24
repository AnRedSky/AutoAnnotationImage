"""
Segmentation API: Mask 标注 CRUD + 训练/自动标注 (v2.0.0 图像分割)
====================================================================

端点 (S5 验收):
- POST   /api/segmentation/masks/upload/{image_id}  multipart 上传 mask PNG
- GET    /api/segmentation/masks/{image_id}          拉取单图 mask (二进制流 + 元数据)
- DELETE /api/segmentation/masks/{mask_id}           删除
- POST   /api/segmentation/masks/replace             单图 mask 全量替换
- POST   /api/segmentation/train                    启动分割训练 (Celery)
- POST   /api/segmentation/auto-annotate            启动自动分割标注 (Celery)
- GET    /api/segmentation/jobs/{job_id}/progress   老接口 (int job_id)
- GET    /api/segmentation/progress/{task_id}       v2.5.35 新增 (Celery UUID, REST)
- GET    /api/segmentation/progress/stream/{task_id} v2.5.35 新增 (Celery UUID, SSE)

约定:
- mask 物理存储: PNG 索引图 (P-mode, L-mode 也可), 像素值 = 类别索引
- 0 = 背景 (未标注); 像素值 N = Category.id = N 的类别 (按 Category.id 升序映射)
- mask_path 相对 UPLOAD_DIR, 写入 storage_service
- 仅 segmentation 数据集允许操作
- 一张图唯一一条 mask (UNIQUE image_id)

v2.5.35 关键修复:
- 前端 segmentationApi.progress 调 /api/segmentation/progress/{taskId} (Celery UUID),
  老接口只支持 int job_id → 永远 404. 新增与前端路径对齐的端点.
- auto_annotate_segmentation_task 不写 TrainingJob, 进度端点必须支持
  「DB 没记录就回退到 Celery result.info」路径.
"""
import io
import json
import asyncio
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.core.deps import get_current_user, get_user_optional_for_query
from app.core.celery_utils import check_celery_available as _check_celery_available
from app.models.user import User
from app.models.image import Image
from app.models.category import Category
from app.models.segmentation_mask import SegmentationMask
from app.models.dataset import Dataset
from app.models.model_version import ModelVersion
from app.models.training_job import TrainingJob
from app.schemas.enums import TaskType, AnnotationSource
from app.services.storage_service import storage_service
# v3.0.0 Phase 4: 业务编排下沉到 Service
from app.services import SegmentationService
from PIL import Image as PILImage

router = APIRouter()


# ============== 工具 ==============

async def _ensure_segmentation_image(image_id: int, db: AsyncSession) -> Image:
    """校验图片存在且为 segmentation 任务类型"""
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, f"Image id={image_id} not found")
    if img.task_type != TaskType.SEGMENTATION.value:
        raise HTTPException(
            400,
            f"Image id={image_id} task_type is {img.task_type!r}, "
            f"expected 'segmentation'",
        )
    return img


def _read_mask_png(content: bytes) -> tuple[int, int, dict[int, int]]:
    """
    解析 PNG 索引图, 返回 (width, height, {pixel_value: count})

    - 仅接受 P-mode 或 L-mode
    - 自动转 P-mode (如果原图是 L, 等价)
    - 统计每种像素值的数量 (用于 category_pixel_counts)
    """
    try:
        pil = PILImage.open(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(400, f"无法解析 PNG: {e}")

    if pil.mode == "P":
        # P-mode: 索引值就是类别索引
        arr = pil
    elif pil.mode == "L":
        # L-mode: 灰度值直接当类别索引
        arr = pil
    elif pil.mode in ("RGB", "RGBA"):
        # v2.5.0-s12.8: 前端 canvas.toBlob 默认输出 RGBA PNG (color type 6),
        # 实际数据是 R=G=B=category_id, A=255. 取 R 通道当 L-mode 保持下游 arr.getdata() 兼容.
        # 业务上: mask 是单通道索引图, 调色板只是显示层.
        arr = pil.getchannel("R")  # PIL Image (L-mode), 灰度值 = R = category_id
    elif pil.mode == "1":
        # 二值图, 255/0 当类别
        arr = pil.convert("L")
    else:
        raise HTTPException(
            400, f"mask PNG 必须是 P-mode (索引) / L-mode (灰度) / RGB / RGBA, 当前 {pil.mode!r}",
        )

    # 统计像素值分布
    pixels = list(arr.getdata())
    counts: dict[int, int] = {}
    for v in pixels:
        counts[int(v)] = counts.get(int(v), 0) + 1
    return pil.size[0], pil.size[1], counts


# ============== CRUD ==============

@router.post("/masks/upload/{image_id}")
async def upload_mask(
    image_id: int,
    file: UploadFile = File(..., description="mask PNG (P/L mode)"),
    source: str = Query("human", description="ai / human / human_corrected"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    上传 / 替换单图 mask (v3.0.0 Phase 4: thin wrapper, 业务下沉到 SegmentationService.save_uploaded_mask)
    - 同一 image_id 多次上传, 后者覆盖前者
    - 自动读 PNG, 校验 mode, 写 storage_service, mask_path 存到 ORM
    - 写完统计 category_pixel_counts (内存返回, 不入库)
    """
    img = await _ensure_segmentation_image(image_id, db)

    # source 校验
    if source not in {s.value for s in AnnotationSource}:
        raise HTTPException(400, f"source 非法: {source!r}")

    content = await file.read()
    return await SegmentationService.save_uploaded_mask(
        db, img, content, source, user_id=current_user.id,
    )


@router.get("/masks/{image_id}")
async def get_mask(
    image_id: int,
    download: bool = Query(False, description="True=直接返回 PNG 二进制"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    拉取单图 mask
    - download=False: 返回 JSON 元数据 (含 base64 摘要, 不含完整二进制)
    - download=True:  返回 PNG 二进制 (Content-Type: image/png)
    """
    img = await _ensure_segmentation_image(image_id, db)
    m = (await db.execute(
        select(SegmentationMask).where(SegmentationMask.image_id == image_id)
    )).scalar_one_or_none()
    if not m:
        # v2.5.0-s12.7: mask 未标注是常态 (新图), 不抛 404
        # 改为 200 + {file_exists: false}, 前端 loadSegmentationMask 已能识别
        # 避免 F12 Network 大量红色 404 噪音
        return {
            "id": None,
            "image_id": image_id,
            "mask_path": None,
            "width": None,
            "height": None,
            "source": None,
            "annotated_by": None,
            "file_exists": False,
            "file_size": None,
            "created_at": None,
            "updated_at": None,
        }

    if download:
        # 返回 PNG 流
        abs_path = Path(storage_service.base_dir) / m.mask_path
        if not abs_path.is_file():
            raise HTTPException(500, f"mask 文件不存在: {m.mask_path}")
        return Response(
            content=abs_path.read_bytes(),
            media_type="image/png",
            headers={
                "Content-Disposition": (
                    f'inline; filename="mask_{image_id}.png"'
                ),
            },
        )

    # JSON 元数据
    abs_path = Path(storage_service.base_dir) / m.mask_path
    file_exists = abs_path.is_file()
    file_size = abs_path.stat().st_size if file_exists else None
    return {
        "id": m.id,
        "image_id": m.image_id,
        "mask_path": m.mask_path,
        "width": m.width,
        "height": m.height,
        "source": m.source,
        "annotated_by": m.annotated_by,
        "file_exists": file_exists,
        "file_size": file_size,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


@router.delete("/masks/{mask_id}")
async def delete_mask(
    mask_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    删除 mask (v3.0.0 Phase 4: thin wrapper, 业务下沉到 SegmentationService.delete_mask)
    - ORM 软记录直接删, 磁盘文件同时清理
    """
    deleted = await SegmentationService.delete_mask(db, mask_id)
    if not deleted:
        raise HTTPException(404, f"Mask id={mask_id} not found")
    return {
        "success": True,
        "deleted_id": mask_id,
        "file_deleted": True,
    }


@router.post("/masks/replace")
async def replace_mask(
    image_id: int = Query(..., description="图片 id"),
    file: UploadFile = File(..., description="新 mask PNG"),
    source: str = Query("human"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单图 mask 替换: 走 upload 内部逻辑 (覆盖式), 暴露语义化入口
    实际是 upload_mask 的别名, 方便前端批量替换流程
    """
    return await upload_mask(
        image_id=image_id, file=file, source=source,
        db=db, current_user=current_user,
    )


# ============== S5.2 训练 / 自动标注 / 进度 ==============
# Redis 健康检查统一使用 app.core.celery_utils.check_celery_available


@router.post("/train")
async def start_segmentation_train(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    启动 DeepLabV3+ 训练任务 (Celery)

    payload 字段:
      dataset_id: int
      backbone: str = 'deeplabv3_resnet50'
      model_name: str = ''  (留空 -> {backbone}_run)
      epochs: int = 3
      batch_size: int = 4
      crop_size: int = 256
      learning_rate: float = 1e-4
    """
    from app.models.dataset import Dataset
    from app.models.training_job import TrainingJob
    from app.schemas.detection import DetectionTrainRequest  # 复用 schema 结构
    from app.schemas.enums import TaskType

    _check_celery_available()

    ds_id = int(payload.get("dataset_id", 0))
    if not ds_id:
        raise HTTPException(400, "dataset_id 必填")
    ds = await db.get(Dataset, ds_id)
    if not ds:
        raise HTTPException(404, f"Dataset id={ds_id} not found")
    if ds.task_type != TaskType.SEGMENTATION.value:
        raise HTTPException(
            400,
            f"Dataset task_type={ds.task_type!r}, expected 'segmentation'",
        )
    if ds.owner_id and ds.owner_id != current_user.id:
        raise HTTPException(403, "仅数据集 owner 可启动训练")

    backbone = (payload.get("backbone") or "deeplabv3_resnet50").strip()
    model_alias = (payload.get("model_name") or "").strip() or f"{backbone}_run"
    epochs = int(payload.get("epochs") or 3)
    batch_size = int(payload.get("batch_size") or 4)
    crop_size = int(payload.get("crop_size") or 256)
    learning_rate = float(payload.get("learning_rate") or 1e-4)

    from app.workers.segmentation_tasks import train_segmentation_task
    try:
        async_result = train_segmentation_task.delay(
            dataset_id=ds_id, user_id=current_user.id,
            backbone=backbone, model_alias=model_alias,
            epochs=epochs, batch_size=batch_size,
            crop_size=crop_size, learning_rate=learning_rate,
            device="cpu",
        )
    except Exception as e:
        raise HTTPException(503, f"Celery .delay() 失败: {e}")

    return {
        "task_id": async_result.id,
        "celery_task_id": async_result.id,
        "job_id": 0,
        "state": "PENDING",
        "message": "分割训练任务已入队, 等待 worker 启动...",
    }


@router.post("/auto-annotate")
async def start_segmentation_auto_annotate(
    dataset_id: int = Query(..., description="segmentation 数据集 id"),
    model_version_id: int = Query(..., description="用于推理的 ModelVersion id"),
    overwrite_existing: bool = Query(False),
    crop_size: int = Query(256, ge=64, le=1024),
    device: str = Query("cpu"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """启动自动分割标注"""
    from app.schemas.enums import TaskType
    _check_celery_available()

    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset id={dataset_id} not found")
    if ds.task_type != TaskType.SEGMENTATION.value:
        raise HTTPException(400, f"非 segmentation 数据集: {ds.task_type}")

    mv = await db.get(ModelVersion, model_version_id)
    if not mv:
        raise HTTPException(404, f"ModelVersion id={model_version_id} not found")
    if mv.task_type != TaskType.SEGMENTATION.value:
        raise HTTPException(
            400, f"ModelVersion task_type={mv.task_type!r}, expected 'segmentation'",
        )

    from app.workers.segmentation_tasks import auto_annotate_segmentation_task
    try:
        async_result = auto_annotate_segmentation_task.delay(
            dataset_id=dataset_id, user_id=current_user.id,
            model_version_id=model_version_id,
            overwrite_existing=overwrite_existing,
            crop_size=crop_size, device=device,
        )
    except Exception as e:
        raise HTTPException(503, f"Celery .delay() 失败: {e}")

    return {
        "task_id": async_result.id,
        "state": "PENDING",
        "message": "分割自动标注任务已入队...",
    }


@router.get("/jobs/{job_id}/progress")
async def get_segmentation_job_progress(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """拉取分割 TrainingJob 进度 (轮询)"""
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
        "current_epoch": getattr(job, "current_epoch", None),
        "duration_seconds": job.duration_seconds,
        "model_version_id": job.model_version_id,
    }


# ============== v2.5.35 新增: 与前端 segmentationApi 路径对齐的进度端点 ==============
# 前端 segmentationApi.progress 调 GET /api/segmentation/progress/{taskId} (Celery UUID).
# 老的 /jobs/{job_id}/progress 端点保留 (int TrainingJob id), 但前端轮询走新端点.

async def _resolve_segmentation_task_progress(task_id: str) -> dict:
    """统一解析 Celery task_id -> 进度 dict, 同时覆盖训练 + 自动标注两种来源.

    优先级:
    1. TrainingJob 表 (训练任务走的是 _create_job 路径, celery_task_id 是主键索引列)
    2. Celery AsyncResult (auto_annotate_segmentation_task 不写 TrainingJob, 只走 update_state)
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
        try:
            if celery_state == "SUCCESS":
                celery_result_payload = result.result
        except Exception:
            celery_result_payload = None
    except Exception:
        pass

    if db_row is not None:
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

    return {
        "task_id": task_id,
        "state": celery_state,
        "progress": round(float(celery_info.get("progress", 0)), 2),
        "message": celery_info.get("msg") or celery_info.get("info") or "",
        "current_epoch": celery_info.get("current_epoch") or celery_info.get("epoch"),
        "total_epochs": celery_info.get("total_epochs"),
        "result": celery_result_payload if isinstance(celery_result_payload, dict) else None,
        "source": "celery" if celery_state != "PENDING" else "unknown",
    }


@router.get("/progress/{task_id}")
async def get_segmentation_progress(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    拉取 segmentation 任务进度 (REST 轮询)

    v2.5.35 新增: 与前端 segmentationApi.progress 路径对齐
    """
    return await _resolve_segmentation_task_progress(task_id)


@router.get("/progress/stream/{task_id}")
async def stream_segmentation_progress(
    task_id: str,
    request: Request,
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_user_optional_for_query),
):
    """
    SSE 实时推送 segmentation 任务进度 (v2.5.35 新增)
    """
    if current_user is None:
        raise HTTPException(401, "未授权: 需要有效的 access_token (query ?token= 或 Authorization header)")

    SSE_SEG_POLL_INTERVAL = 1.0

    async def event_gen():
        last_signature: Optional[tuple] = None
        try:
            yield f": connected task_id={task_id}\n\n"
        except Exception:
            return

        while True:
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break

            try:
                data = await _resolve_segmentation_task_progress(task_id)
            except Exception as e:
                data = {
                    "task_id": task_id,
                    "state": "FAILURE",
                    "progress": 0.0,
                    "message": f"进度查询失败: {type(e).__name__}: {str(e)[:200]}",
                }

            payload = {
                "task_id": data["task_id"],
                "state": data["state"],
                "progress": data["progress"],
                "message": data["message"],
            }
            for k in ("current_epoch", "total_epochs",
                      "data_total", "data_train", "data_val",
                      "num_classes", "class_names", "model_version_id",
                      "started_at", "finished_at", "duration_seconds", "source"):
                v = data.get(k)
                if v is not None:
                    payload[k] = v
            if data.get("result") and isinstance(data["result"], dict):
                for k in ("status", "total", "auto_labeled", "no_match"):
                    if k in data["result"]:
                        payload.setdefault(k, data["result"][k])

            signature = (payload["state"], round(payload["progress"], 1), payload["message"])
            if signature != last_signature:
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                last_signature = signature

            if payload["state"] in ("SUCCESS", "FAILURE", "REVOKED"):
                try:
                    yield "event: end\ndata: {}\n\n"
                except Exception:
                    pass
                break

            await asyncio.sleep(SSE_SEG_POLL_INTERVAL)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
