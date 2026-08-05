"""
segmentation.train 模块 — 训练 + 自动标注
=========================================

**v3.0.0 Phase S2 拆分**: 从 segmentation.py 抽离
**职责**: 启动分割训练任务 + 启动自动分割标注任务

**路由清单** (2 个):
- POST /train                    启动 DeepLabV3+ 训练 (Celery)
- POST /auto-annotate            启动自动分割标注 (Celery)

**S2 启动约定**:
- 强制 dataset.task_type == "segmentation", 否则 400
- 仅 owner 可启动训练 (ds.owner_id == current_user.id)
- payload 字段: dataset_id, backbone, model_name, epochs, batch_size, crop_size, learning_rate
- 自动标注额外需要: model_version_id, overwrite_existing, crop_size, device
- 启动前通过 check_celery_available() 验证 Celery 可用
- 返回 { task_id, state: "PENDING", ... } 给前端轮询
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.dataset import Dataset
from app.tasks.model.model_version import ModelVersion
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.utils.async_helpers import check_celery_available as _check_celery_available
from app.common.enums import TaskType

router = APIRouter()


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
    # v3.3.6-STATS-ISOLATION: 统一走 assert_can_access_dataset (含 team_member 共享)
    # 旧逻辑仅校验 owner, 团队成员无法启动训练 (横向越权修复)
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, ds, require_write=True)

    backbone = (payload.get("backbone") or "deeplabv3_resnet50").strip()
    model_alias = (payload.get("model_name") or "").strip() or f"{backbone}_run"
    epochs = int(payload.get("epochs") or 3)
    batch_size = int(payload.get("batch_size") or 4)
    crop_size = int(payload.get("crop_size") or 256)
    learning_rate = float(payload.get("learning_rate") or 1e-4)

    from app.tasks.workers.segmentation import train_segmentation_task
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
    # v3.3.6-STATS-ISOLATION: 校验 dataset 写权限 (含 team_member 共享)
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, ds, require_write=True)

    from app.tasks.workers.segmentation import auto_annotate_segmentation_task
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
