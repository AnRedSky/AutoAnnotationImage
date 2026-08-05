"""
detection.train 模块 — 目标检测训练启动 + 自动标注
=================================================

**v3.0.0 Phase P 拆分**: 从 detection.py 抽离
**职责**: 训练任务投递 + 自动标注 (fine-tune / 预训练) 启动

**路由清单** (3 个):
- POST /train                    启动 YOLOv8 训练 (Celery)
- POST /auto-annotate            启动 fine-tune 模型自动标注
- POST /auto-annotate-pretrained 启动预训练 YOLO 自动标注 (无需 ModelVersion)

**Redis 健康检查**: 统一使用 app.utils.async_helpers.check_celery_available
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.utils.async_helpers import check_celery_available as _check_celery_available
from app.middleware.http.auth import get_current_user
from app.admin.model.user import User
from app.tasks.model.dataset import Dataset
from app.tasks.model.model_version import ModelVersion
from app.common.enums import TaskType
from app.schemas.detection import (
    DetectionTrainRequest, DetectionTrainResponse,
)

router = APIRouter()


# ============== COCO 80 类 (YOLOv8 预训练权重内置) ==============

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


# ============== 端点 ==============

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
    # 权限: 必须对该 dataset 有写权限 (v3.3.6-STATS-ISOLATION 统一走 assert_can_access_dataset)
    # 旧逻辑仅校验 owner, 团队成员无法启动训练 (横向越权修复)
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, ds, require_write=True)

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
    # v3.3.6-STATS-ISOLATION: 校验 dataset 写权限 (含 team_member 共享, 修旧版越权)
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, ds, require_write=True)

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


# v2.3.2: 用 ultralytics 预训练 yolov8n/s/m/l/x 做自动标注 (无需 ModelVersion)
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
    # v3.3.6-STATS-ISOLATION: 校验 dataset 写权限 (旧版无校验, 任何用户可启动)
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, ds, require_write=True)

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
