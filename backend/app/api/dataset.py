"""
Dataset API: CRUD + Category Management
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update as sa_update, func
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
    """
    列出数据集的所有类别, 同时返回每个类别的**实时**统计信息

    v2.5.18 修复 (类别统计):
      之前只按 Image.final_label_id (分类场景) + Image.ai_prediction.top1 (分类 AI 候选) 统计,
      检测 (BBoxAnnotation) / 分割 (SegmentationMask) 表的样本数据被完全忽略,
      导致 DatasetDetail 类别管理弹窗"已确认 / AI 已标 / 总样本" 全是 0
    修复: 按 dataset.task_type 分派:
      - classification: 原逻辑 (final_label_id + ai_prediction.top1)
      - detection:      统计 BBoxAnnotation 行数, 按 category_id + source (human/ai) 分桶
      - segmentation:   读每张 mask PNG 解析 category_pixel_counts,
                        按 (image 包含的类别) + source 分桶, 一张图有 N 类算 N 次"出现"

    返回字段 (各类别):
      - human_labeled_count: 人工已标 (source ∈ human/human_corrected)
      - ai_labeled_count:    AI 已标 (source = ai)
      - ai_candidate_count:  AI 候选 (仅分类有效, 检测/分割恒为 0)
      - sample_count:        human + ai 之和
    """
    from app.models.image import Image
    from app.models.bbox_annotation import BBoxAnnotation
    from app.models.segmentation_mask import SegmentationMask
    from app.services.storage_service import storage_service
    from pathlib import Path as _P
    from PIL import Image as _PIL
    import io as _io

    # 0) 拿 dataset.task_type 以决定统计策略
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, f"Dataset id={dataset_id} not found")
    task_type: str = dataset.task_type or "classification"

    # 1) 取本数据集全部 category
    result = await db.execute(
        select(Category).where(Category.dataset_id == dataset_id).order_by(Category.sort_order)
    )
    cats = result.scalars().all()
    if not cats:
        return {"items": []}

    name_to_id = {c.name: c.id for c in cats}
    stats: dict = {c.id: {"human": 0, "ai": 0, "candidate": 0} for c in cats}

    # ============== 分派: classification (原逻辑) ==============
    if task_type == "classification":
        # 2) 一次性聚合: 拉出本数据集所有"有 ai_prediction 或已 final_label"的图片,
        #    在 Python 端分桶聚合 (避免多表 JOIN + JSON 提取, 跨 SQLite/MySQL 兼容)
        stmt = select(
            Image.final_label_id,
            Image.status,
            Image.ai_prediction,
        ).where(
            Image.dataset_id == dataset_id,
            # 只筛有 final_label 或有 ai_prediction 的图, 减少空扫
            (Image.final_label_id.isnot(None)) | (Image.ai_prediction.isnot(None)),
        )
        rows = (await db.execute(stmt)).all()

        for final_label_id, status, ai_pred in rows:
            # 1) 已在某 category 上的 (按 final_label_id 分桶)
            if final_label_id is not None and final_label_id in stats:
                if status in ("human_confirmed", "human_corrected", "trained"):
                    stats[final_label_id]["human"] += 1
                elif status == "ai_labeled":
                    stats[final_label_id]["ai"] += 1
                # 其它状态不会带 final_label_id, 这里不需考虑

            # 2) AI 候选: final_label_id 为空, 但 ai_prediction.top1 命中本数据集某个 category 名
            if final_label_id is None and ai_pred:
                top1 = (ai_pred or {}).get("top1") if isinstance(ai_pred, dict) else None
                if top1 and top1 in name_to_id:
                    stats[name_to_id[top1]]["candidate"] += 1

    # ============== 分派: detection (BBoxAnnotation) ==============
    elif task_type == "detection":
        # 按 category_id + source 分桶
        stmt = (
            select(
                BBoxAnnotation.category_id,
                BBoxAnnotation.source,
                func.count(BBoxAnnotation.id),
            )
            .join(Image, Image.id == BBoxAnnotation.image_id)
            .where(Image.dataset_id == dataset_id)
            .group_by(BBoxAnnotation.category_id, BBoxAnnotation.source)
        )
        rows = (await db.execute(stmt)).all()
        for cat_id, source, cnt in rows:
            if cat_id is None or cat_id not in stats:
                continue  # 兜底: bbox 可能 category_id=NULL (非法) 或属于已删类别
            if source in ("human", "human_corrected"):
                stats[cat_id]["human"] += int(cnt)
            elif source == "ai":
                stats[cat_id]["ai"] += int(cnt)
            # 其它 source: 忽略

    # ============== 分派: segmentation (SegmentationMask + 解析 PNG) ==============
    elif task_type == "segmentation":
        # 拉所有 mask 的 source + 物理路径
        stmt = (
            select(
                SegmentationMask.source,
                SegmentationMask.mask_path,
            )
            .join(Image, Image.id == SegmentationMask.image_id)
            .where(Image.dataset_id == dataset_id)
        )
        mask_rows = (await db.execute(stmt)).all()

        # 一次 Python 循环: 读 PNG, 解析像素分布, 按 (image 内含的 cat_id, source) +1
        # 性能: mask 通常是 100x100 ~ 500x500, 单文件解析 < 5ms; 100 张图 < 500ms 可接受
        for source, mask_path in mask_rows:
            try:
                abs_path = _P(storage_service.base_dir) / mask_path
                if not abs_path.is_file():
                    continue
                with open(abs_path, "rb") as f:
                    content = f.read()
                pil = _PIL.open(_io.BytesIO(content))
                # 与 segmentation.py 保持一致的解析规则
                if pil.mode == "P":
                    arr = pil
                elif pil.mode == "L":
                    arr = pil
                elif pil.mode in ("RGB", "RGBA"):
                    arr = pil.getchannel("R")
                elif pil.mode == "1":
                    arr = pil.convert("L")
                else:
                    continue
                # 收集"本 mask 包含哪些 cat_id" (0 跳过, 0 = 背景)
                present_cats = set()
                for v in arr.getdata():
                    cv = int(v)
                    if cv == 0:
                        continue  # 0 = 背景, 不算任何类别的样本
                    if cv in stats:
                        present_cats.add(cv)
                # 增量
                for cat_id in present_cats:
                    if source in ("human", "human_corrected"):
                        stats[cat_id]["human"] += 1
                    elif source == "ai":
                        stats[cat_id]["ai"] += 1
                    # 其它 source 忽略
            except Exception:
                # 单张 mask 解析失败不影响整批, 跳过即可
                continue

    # 其它未识别 task_type: 当作 classification 兜底 (空统计)

    return {
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "color": c.color,
                # 实时计算, 不再依赖 Category.sample_count 缓存字段
                "sample_count": stats[c.id]["human"] + stats[c.id]["ai"],
                "human_labeled_count": stats[c.id]["human"],
                "ai_labeled_count": stats[c.id]["ai"],
                "ai_candidate_count": stats[c.id]["candidate"],
            }
            for c in cats
        ]
    }
