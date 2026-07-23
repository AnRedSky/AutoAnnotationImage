"""
Segmentation API: Mask 标注 CRUD + 训练/自动标注 (v2.0.0 图像分割)
====================================================================

端点 (S5 验收):
- POST   /api/segmentation/masks/upload/{image_id}  multipart 上传 mask PNG
- GET    /api/segmentation/masks/{image_id}          拉取单图 mask (二进制流 + 元数据)
- DELETE /api/segmentation/masks/{mask_id}           删除
- POST   /api/segmentation/masks/replace             单图 mask 全量替换

约定:
- mask 物理存储: PNG 索引图 (P-mode, L-mode 也可), 像素值 = 类别索引
- 0 = 背景 (未标注); 像素值 N = Category.id = N 的类别 (按 Category.id 升序映射)
- mask_path 相对 UPLOAD_DIR, 写入 storage_service
- 仅 segmentation 数据集允许操作
- 一张图唯一一条 mask (UNIQUE image_id)
"""
import io
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.core.deps import get_current_user
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
    上传 / 替换单图 mask (同一 image_id 多次上传, 后者覆盖前者)
    - 自动读 PNG, 校验 mode
    - 写入 storage_service, mask_path 存到 ORM
    - 写完统计 category_pixel_counts (内存返回, 不入库)
    """
    img = await _ensure_segmentation_image(image_id, db)

    # source 校验
    if source not in {s.value for s in AnnotationSource}:
        raise HTTPException(400, f"source 非法: {source!r}")

    content = await file.read()
    if not content:
        raise HTTPException(400, "上传的 mask 文件为空")
    width, height, counts = _read_mask_png(content)

    # 校验: 像素值不应超过 dataset 类别数
    cats = (await db.execute(
        select(Category).where(Category.dataset_id == img.dataset_id)
    )).scalars().all()
    max_allowed = max([c.id for c in cats], default=0)
    overflow = [v for v in counts.keys() if v > max_allowed]
    if overflow:
        raise HTTPException(
            400,
            f"mask 像素值超过 dataset 类别数 (max_category_id={max_allowed}, "
            f"overflow={sorted(overflow)[:5]}...)",
        )

    # 写盘: mask 路径单独命名, 避免与 image 冲突
    file_hash = storage_service.compute_hash(content)
    storage_key = storage_service.generate_key(
        img.dataset_id, f"mask_{image_id}.png", file_hash,
    )
    # 强制 .png 后缀
    if not storage_key.endswith(".png"):
        storage_key = f"{storage_key}.png"
    await storage_service.save(storage_key, content)

    # 查/删/写 ORM (单图唯一)
    existing = (await db.execute(
        select(SegmentationMask).where(SegmentationMask.image_id == image_id)
    )).scalar_one_or_none()
    if existing:
        # 删旧文件 (如果存在, 路径不同才删)
        if existing.mask_path and existing.mask_path != storage_key:
            try:
                old_path = Path(storage_service.base_dir) / existing.mask_path
                if old_path.is_file():
                    old_path.unlink()
            except Exception:
                pass
        existing.mask_path = storage_key
        existing.width = width
        existing.height = height
        existing.source = source
        existing.annotated_by = current_user.id
        m = existing
    else:
        m = SegmentationMask(
            image_id=image_id,
            mask_path=storage_key,
            width=width,
            height=height,
            source=source,
            annotated_by=current_user.id,
        )
        db.add(m)

    await db.commit()
    await db.refresh(m)
    # v2.5.15: mask 上传成功后, 把 image.status 提升到 human_confirmed
    # - 之前只 insert/update SegmentationMask, image.status 一直停留在 pending/ai_labeled
    # - 导致前端 stats 的"待标注"数字永远不减
    # - 仅当原状态是 pending/ai_labeled 时才升级, 不降级 (保留 human_corrected 语义)
    if img.status in ('pending', 'ai_labeled'):
        img.status = 'human_confirmed'
        await db.commit()
        await db.refresh(img)

    # 把 counts 转成 {category_id: pixel_count}, 0 也保留 (代表背景)
    return {
        "id": m.id,
        "image_id": m.image_id,
        "mask_path": m.mask_path,
        "width": m.width,
        "height": m.height,
        "source": m.source,
        "annotated_by": m.annotated_by,
        "category_pixel_counts": {int(k): int(v) for k, v in counts.items()},
    }


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
    删除 mask
    - ORM 软记录直接删, 磁盘文件同时清理
    """
    m = await db.get(SegmentationMask, mask_id)
    if not m:
        raise HTTPException(404, f"Mask id={mask_id} not found")

    # 尝试删文件
    file_deleted = False
    if m.mask_path:
        try:
            abs_path = Path(storage_service.base_dir) / m.mask_path
            if abs_path.is_file():
                abs_path.unlink()
                file_deleted = True
        except Exception:
            pass

    await db.delete(m)
    await db.commit()
    return {
        "success": True,
        "deleted_id": mask_id,
        "file_deleted": file_deleted,
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
