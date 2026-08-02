"""
image.delete 模块 — 图片删除 API
==================================

v3.0.0 Phase N 拆分: 从 image.py 抽离
- 职责: 单图删除 + 批量删除 (业务下沉到 ImageService)
- 关键: eager load bbox_annotations / segmentation_mask, 否则 ORM cascade 不触发
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.image import Image
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.service.image_service import ImageService

# delete 独立 router
router = APIRouter()


@router.delete("/{image_id}")
async def delete_image(
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    删除单张图片 (v3.0.0 Phase 4: thin wrapper, 业务下沉到 ImageService.delete)

    业务规则 (全部在 service 层):
    - 删文件 (storage_service)
    - 删 AnnotationLog (CASCADE 已配)
    - 删 BBoxAnnotation / SegmentationMask (ORM cascade='all, delete-orphan')
    - 更新 dataset.image_count / annotated_count

    关键: 必须 eager load 关联 (bbox_annotations / segmentation_mask), 否则
    SQLAlchemy 的 ORM cascade 不会触发, bbox/mask 会残留

    v3.3.0 P0 修复: 必须校验写权限
    """
    stmt = (
        select(Image)
        .where(Image.id == image_id)
        .options(
            selectinload(Image.bbox_annotations),
            selectinload(Image.segmentation_mask),
        )
    )
    img = (await db.execute(stmt)).scalar_one_or_none()
    if not img:
        raise HTTPException(404, "Image not found")

    # v3.3.0 P0: 权限校验
    from app.tasks.model.dataset import Dataset
    from app.tasks.service.permission_service import assert_can_access_dataset
    dataset = await db.get(Dataset, img.dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    await assert_can_access_dataset(db, current_user, dataset, require_write=True)

    return await ImageService.delete(db, img)


@router.post("/batch-delete")
async def batch_delete_images(
    image_ids: List[int],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量删除图片 (v3.0.0 Phase 4: thin wrapper, 业务下沉到 ImageService.batch_delete)
    Body: { "image_ids": [1, 2, 3] } (或直接数组)

    v3.3.0 P0 修复: 校验每张图所属 dataset 的写权限
    """
    # 预查: 一次性收集 dataset_id, 整体校验
    if not image_ids:
        return {"deleted": 0, "skipped": 0, "missing": 0, "items": []}
    if len(image_ids) > 500:
        raise HTTPException(400, "Too many ids (max 500)")

    from app.tasks.model.dataset import Dataset
    from app.tasks.service.permission_service import assert_can_access_dataset
    img_rows = (await db.execute(
        select(Image).where(Image.id.in_(image_ids))
    )).scalars().all()
    ds_ids = {img.dataset_id for img in img_rows}
    for ds_id in ds_ids:
        ds = await db.get(Dataset, ds_id)
        if not ds:
            continue  # 找不到的 dataset 跳过, 由 service 层处理 missing
        await assert_can_access_dataset(db, current_user, ds, require_write=True)

    return await ImageService.batch_delete(db, image_ids)
