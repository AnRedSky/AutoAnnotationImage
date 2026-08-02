"""
Dataset API: CRUD + Category Management

**v3.0.0 Phase 3 重构**:
- delete_dataset: 级联删除下沉到 DatasetService.cascade_delete
  (110 行 → 12 行, 业务规则统一)
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update as sa_update, func
from app.core.config import settings
from pydantic import BaseModel

from app.database import get_db
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
# v3.0.0 Phase 3: 业务编排下沉
from app.tasks.service.dataset_service import DatasetService

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
    # P0-1: 非 admin 只看自己的 dataset; admin 看全部
    stmt = select(Dataset).order_by(Dataset.id.desc())
    if not current_user.is_admin():
        stmt = stmt.where(Dataset.owner_id == current_user.id)
    result = await db.execute(stmt)
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
    """获取数据集详情（含类别）

    v3.3.0 P0 修复: 必须校验访问权限 (owner / team_member / admin)
    - 之前: 任何登录用户可通过 ID 拿到任何 dataset 的完整信息
    - 现在: 非授权访问直接 403
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 权限校验
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset)

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
    """删除数据集 (v3.0.0 Phase 3: 业务下沉到 DatasetService.cascade_delete)

    业务规则 (全部在 service 层):
    - 仅 draft / done 状态可删除
    - 显式顺序: training_job → model_version → annotation_log → image → category → dataset
    - 自动清理磁盘: 图片目录 + 模型 .pth 文件

    修复历史 (详见 [project_memory]):
    - 之前只 db.delete(dataset) 会 500, 原因:
      - training_job.dataset_id / model_version.dataset_id 没有 ondelete=CASCADE
      - image.final_label_id -> category.id 也没 ondelete, 删 category 会阻塞
      - 磁盘上 storage/ 目录下的图片文件没清理
    """
    dataset = await DatasetService.get(db, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # v3.3.0: 权限检查升级 (owner + team_member)
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset)

    # 业务规则: 仅 draft / done 可删
    await DatasetService.assert_can_delete(db, dataset)

    counts = await DatasetService.cascade_delete(db, dataset)

    # MT-8: 审计日志
    from app.tasks.service.audit_service import log_audit
    await log_audit(db, user_id=current_user.id, event_type="dataset_deleted",
                    resource_type="dataset", resource_id=dataset_id,
                    detail={"name": dataset.name, "cascade_counts": counts})
    await db.commit()

    return {
        "success": True,
        "deleted_id": dataset_id,
        "cascade_counts": counts,
    }


@router.post("/{dataset_id}/categories")
async def add_category(
    dataset_id: int,
    req: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新增类别 (v3.3.0 P0 修复: 必须校验写权限)"""
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 权限校验 (写权限: viewer 角色被拒)
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset, require_write=True)

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
    from app.tasks.model.image import Image
    from app.annotation.model.bbox_annotation import BBoxAnnotation
    from app.annotation.model.segmentation_mask import SegmentationMask
    from app.common.storage.storage_service import storage_service
    from pathlib import Path as _P
    from PIL import Image as _PIL
    import io as _io

    # 0) 拿 dataset.task_type 以决定统计策略
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, f"Dataset id={dataset_id} not found")

    # v3.3.0 P0 修复: 必须校验访问权限
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset)
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
