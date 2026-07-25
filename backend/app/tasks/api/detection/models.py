"""
detection.models 模块 — 模型管理 + 跨图复制建议
================================================

**v3.0.0 Phase P 拆分**: 从 detection.py 抽离
**职责**: detection 模型激活/取消激活 + 跨图 bbox 复制建议

**路由清单** (3 个):
- POST /models/{model_id}/activate    激活 detection 模型 (单激活不变量)
- POST /models/{model_id}/deactivate  取消激活 detection 模型
- GET  /copy-suggestion/{image_id}    跨图 bbox 复制建议

**S4 模型激活约定**:
- 沿用 v1.0.0 model.py 的 with_for_update() 行锁模式
- 内部实现与 /api/models/{id}/activate 等价, 仅校验 task_type == "detection"
- v2.5.35: 强制单激活不变量 — 同 dataset 其他 active 全部置 False

**S9.3 跨图复制建议** (v2.2.0):
- 目标图的 task_type='detection', 返回按 category_id 分组的平均 bbox
- 仅返回 source_count >= min_source_count 的类别 (避免噪声)
"""
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.http.auth import get_current_user
from app.admin.model.user import User
from app.tasks.model.image import Image
from app.annotation.model.bbox_annotation import BBoxAnnotation
from app.tasks.model.model_version import ModelVersion
from app.common.enums import TaskType

router = APIRouter()


# ============== 行锁工具 ==============

async def _lock_dataset_models(db: AsyncSession, dataset_id: Optional[int]) -> None:
    """锁住指定 dataset 的所有 ModelVersion 行 (SELECT ... FOR UPDATE)"""
    stmt = select(ModelVersion)
    if dataset_id is not None:
        stmt = stmt.where(ModelVersion.dataset_id == dataset_id)
    stmt = stmt.with_for_update()
    (await db.execute(stmt)).scalars().all()


# ============== 模型激活/取消 ==============

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
