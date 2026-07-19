"""
Dataset API: CRUD + Category Management
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update as sa_update
from app.config import settings
from pydantic import BaseModel

from app.database import get_db
from app.models.dataset import Dataset
from app.models.category import Category
from app.models.user import User
from app.core.deps import get_current_user

router = APIRouter()


class DatasetCreate(BaseModel):
    name: str
    description: Optional[str] = None
    task_type: Optional[str] = "classification"
    category_names: Optional[List[str]] = None  # 创建时同时创建类别


class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    color: Optional[str] = "#409EFF"


@router.post("")
async def create_dataset(
    req: DatasetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dataset = Dataset(
        name=req.name,
        description=req.description,
        task_type=req.task_type or "classification",
        owner_id=current_user.id,
    )
    db.add(dataset)
    await db.flush()  # 拿到 dataset.id

    # 批量创建类别
    created_cats = []
    if req.category_names:
        for idx, name in enumerate(req.category_names):
            cat = Category(
                dataset_id=dataset.id,
                name=name.strip(),
                sort_order=idx,
            )
            db.add(cat)
            created_cats.append(name)
        dataset.category_count = len(created_cats)

    await db.commit()
    await db.refresh(dataset)
    return {
        "id": dataset.id,
        "name": dataset.name,
        "task_type": dataset.task_type,
        "category_count": dataset.category_count,
        "categories_created": created_cats,
    }


@router.get("")
async def list_datasets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Dataset).order_by(Dataset.id.desc()))
    datasets = result.scalars().all()
    return {
        "items": [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "task_type": d.task_type,
                "image_count": d.image_count,
                "annotated_count": d.annotated_count,
                "category_count": d.category_count,
                "status": d.status,
                "created_at": d.created_at.isoformat(),
            }
            for d in datasets
        ]
    }


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取数据集详情（含类别）"""
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 列出类别
    result = await db.execute(
        select(Category).where(Category.dataset_id == dataset_id).order_by(Category.sort_order)
    )
    cats = result.scalars().all()

    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "task_type": dataset.task_type,
        "image_count": dataset.image_count,
        "annotated_count": dataset.annotated_count,
        "category_count": dataset.category_count,
        "status": dataset.status,
        "owner_id": dataset.owner_id,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "categories": [
            {"id": c.id, "name": c.name, "color": c.color, "sample_count": c.sample_count}
            for c in cats
        ],
    }


@router.delete("/{dataset_id}")
async def delete_dataset(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除数据集 (级联删除其下图片/类别/标注/训练任务/模型版本, 并清理磁盘文件)

    修复历史: 之前只 db.delete(dataset) 会 500, 原因:
      - training_job.dataset_id / model_version.dataset_id 没有 ondelete=CASCADE
      - image.final_label_id -> category.id 也没 ondelete, 删 category 会阻塞
      - 磁盘上 storage/ 目录下的图片文件没清理
    """
    from sqlalchemy import delete as sa_delete
    from app.models.image import Image
    from app.models.annotation_log import AnnotationLog
    from app.models.training_job import TrainingJob
    from app.models.model_version import ModelVersion
    from app.models.category import Category

    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 1. 先取本数据集下所有 image id, 用于清 annotation_log 和磁盘文件
    img_rows = (await db.execute(
        select(Image.id, Image.storage_path).where(Image.dataset_id == dataset_id)
    )).all()
    img_ids = [r[0] for r in img_rows]
    storage_paths = [r[1] for r in img_rows if r[1]]

    # 2. 清 annotation_log (有 image_id 的 ON DELETE CASCADE, 但保险起见显式删)
    if img_ids:
        await db.execute(
            sa_delete(AnnotationLog).where(AnnotationLog.image_id.in_(img_ids))
        )

    # 3. 清 training_job (dataset_id 没有 ON DELETE CASCADE, 必须显式删)
    await db.execute(
        sa_delete(TrainingJob).where(TrainingJob.dataset_id == dataset_id)
    )

    # 4. 清 model_version (同理), 同时取 .pth 路径以便清理磁盘
    mv_rows = (await db.execute(
        select(ModelVersion.file_path).where(ModelVersion.dataset_id == dataset_id)
    )).all()
    pth_paths = [r[0] for r in mv_rows if r[0]]
    await db.execute(
        sa_delete(ModelVersion).where(ModelVersion.dataset_id == dataset_id)
    )

    # 5. 清 image (final_label_id -> category.id 没 cascade, 必须先解引用再删图)
    #    将 final_label_id 置 NULL 后再删 image (ai_prediction 是 JSON, 不需要解)
    if img_ids:
        await db.execute(
            sa_update(Image)
            .where(Image.id.in_(img_ids))
            .values(final_label_id=None)
        )
    await db.execute(
        sa_delete(Image).where(Image.dataset_id == dataset_id)
    )

    # 6. 清 category
    await db.execute(
        sa_delete(Category).where(Category.dataset_id == dataset_id)
    )

    # 7. 最后删 dataset 自身
    await db.delete(dataset)
    await db.commit()

    # 8. 清理磁盘文件 (失败不影响主流程, 但记录 warn)
    from pathlib import Path as _P
    upload_root = settings.UPLOAD_DIR
    cleaned_files = 0
    for rel in storage_paths:
        try:
            f = upload_root / rel
            if f.exists():
                f.unlink()
                cleaned_files += 1
        except OSError as e:
            import logging
            logging.getLogger(__name__).warning(f"failed to delete {rel}: {e}")
    # 清理模型 .pth
    for p in pth_paths:
        try:
            pf = _P(p)
            if pf.exists():
                pf.unlink()
                cleaned_files += 1
        except OSError as e:
            import logging
            logging.getLogger(__name__).warning(f"failed to delete model {p}: {e}")
    # 清理空目录
    try:
        ds_dir = upload_root / str(dataset_id)
        if ds_dir.exists() and not any(ds_dir.iterdir()):
            ds_dir.rmdir()
    except OSError:
        pass

    return {
        "success": True,
        "deleted_id": dataset_id,
        "cleaned_files": cleaned_files,
    }


@router.post("/{dataset_id}/categories")
async def add_category(
    dataset_id: int,
    req: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    cat = Category(
        dataset_id=dataset_id,
        name=req.name,
        description=req.description,
        color=req.color,
    )
    db.add(cat)

    # 更新类别计数
    dataset.category_count = (dataset.category_count or 0) + 1

    await db.commit()
    await db.refresh(cat)
    return {"id": cat.id, "name": cat.name}


@router.get("/{dataset_id}/categories")
async def list_categories(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Category).where(Category.dataset_id == dataset_id).order_by(Category.sort_order)
    )
    cats = result.scalars().all()
    return {
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "color": c.color,
                "sample_count": c.sample_count,
            }
            for c in cats
        ]
    }
