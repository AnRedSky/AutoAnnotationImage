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
    """
    return await ImageService.batch_delete(db, image_ids)
