"""
Stats API: 标注 / 训练 / 系统综合统计
====================================
为产品运营核心图表提供数据源:
  - 各状态图片分布（饼图）
  - AI 节省时间估算（柱状图）
  - 置信度分布（直方图）
  - 每日标注量（折线图）
  - 训练曲线数据
"""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.tasks.model.annotation_log import AnnotationLog
from app.tasks.model.model_version import ModelVersion
from app.tasks.model.training_job import TrainingJob
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.admin.service.stats_service import StatsService

router = APIRouter()


# 单张图片人工标注基准时长（秒），用于估算"AI 节省时间"
HUMAN_BASELINE_SECONDS = 8.0


@router.get("/overview")
async def stats_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    系统总览（Dashboard 顶部 4 个统计卡）

    v3.0.0 Phase D 修复: 委托 StatsService.global_overview
    - 旧: 4 个独立 count() 查询 + 训练任务数误用 ModelVersion.is_active
    - 新: 1 个 service 调用, 复用全局概览逻辑
    """
    overview = await StatsService.global_overview(db)
    # 转换格式与旧版兼容 (前端 Dashboard 已依赖这些 key)
    return {
        "datasets": overview["datasets"]["total"],
        "images": overview["images"]["total"],
        "labeled_images": sum(
            v for k, v in overview["images"]["by_status"].items()
            if k in ("human_confirmed", "human_corrected", "trained")
        ),
        "model_versions": (
            await db.execute(select(func.count(ModelVersion.id)))
        ).scalar() or 0,
        "training_jobs": overview["training_jobs"]["total"],
    }


@router.get("/dataset/{dataset_id}")
async def dataset_stats(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单数据集统计（产品运营核心图表数据）
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 1. 各状态图片数（饼图数据, v3.0.0: 排除不合格图片, 不合格单独统计）
    stmt = (
        select(Image.status, func.count(Image.id))
        .where(
            Image.dataset_id == dataset_id,
            Image.quality_flag.is_(None),  # v3.0.0: 不合格不计入状态饼图
        )
        .group_by(Image.status)
    )
    status_rows = (await db.execute(stmt)).all()
    status_counts = {row[0]: row[1] for row in status_rows}

    # v3.0.0: 不合格图片单独统计 (供前端展示)
    unqualified_count = (await db.execute(
        select(func.count(Image.id)).where(
            Image.dataset_id == dataset_id,
            Image.quality_flag == "unqualified",
        )
    )).scalar() or 0

    # 2. 类别分布（柱状图数据）
    stmt = (
        select(Category.name, func.count(Image.id))
        .join(Image, Image.final_label_id == Category.id, isouter=True)
        .where(Category.dataset_id == dataset_id)
        .group_by(Category.name)
    )
    cat_rows = (await db.execute(stmt)).all()
    category_distribution = {row[0]: row[1] for row in cat_rows}

    # 3. AI 节省时间估算
    ai_labeled = status_counts.get("ai_labeled", 0)
    confirmed = status_counts.get("human_confirmed", 0)
    corrected = status_counts.get("human_corrected", 0)

    # 实际人工标注总耗时
    time_stmt = (
        select(
            func.coalesce(func.sum(AnnotationLog.time_spent_ms), 0),
            func.count(AnnotationLog.id),
        )
        .join(Image, Image.id == AnnotationLog.image_id)
        .where(Image.dataset_id == dataset_id)
    )
    total_ms, total_annos = (await db.execute(time_stmt)).one()
    total_ms = float(total_ms or 0)  # Decimal -> float
    total_annos = int(total_annos or 0)
    actual_seconds = total_ms / 1000.0

    # 基准: 如果全部人工标注
    total_processed = confirmed + corrected + ai_labeled
    baseline_seconds = total_processed * HUMAN_BASELINE_SECONDS
    saved_seconds = max(0, baseline_seconds - actual_seconds)
    saved_ratio = saved_seconds / baseline_seconds if baseline_seconds > 0 else 0.0

    # 4. 标注员人均速度
    avg_seconds = actual_seconds / max(total_annos, 1) if total_annos else 0

    return {
        "status_counts": status_counts,
        "unqualified_count": unqualified_count,  # v3.0.0: 不合格图片数 (正交维度, 不计入 status_counts)
        "category_distribution": category_distribution,
        "annotation": {
            "total_annotations": total_annos or 0,
            "actual_seconds": round(actual_seconds, 2),
            "avg_seconds_per_image": round(avg_seconds, 2),
            "ai_labeled_count": ai_labeled,
            "human_confirmed_count": confirmed,
            "human_corrected_count": corrected,
            "estimated_saved_seconds": round(saved_seconds, 2),
            "estimated_saved_ratio": round(saved_ratio, 4),
            "baseline_seconds_per_image": HUMAN_BASELINE_SECONDS,
        },
    }


@router.get("/confidence/{dataset_id}")
async def confidence_distribution(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    AI 预测置信度分布（直方图数据）
    区间: [0, 0.1), [0.1, 0.2), ..., [0.9, 1.0]
    """
    stmt = select(Image.ai_prediction).where(
        Image.dataset_id == dataset_id,
        Image.ai_prediction.isnot(None),
    )
    rows = (await db.execute(stmt)).scalars().all()

    buckets = [0] * 10
    for row in rows:
        pred = row or {}
        conf = pred.get("top1_conf", 0)
        idx = min(9, int(conf * 10))
        buckets[idx] += 1

    return {
        "buckets": [
            {"range": f"{i / 10:.1f}-{(i + 1) / 10:.1f}", "count": buckets[i]}
            for i in range(10)
        ],
        "total": sum(buckets),
    }


@router.get("/timeline/{dataset_id}")
async def annotation_timeline(
    dataset_id: int,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    每日标注量（折线图数据）
    默认 7 天，可指定 1-90 天
    """
    end = datetime.utcnow().date()
    start = end - timedelta(days=days - 1)

    stmt = (
        select(
            func.date(AnnotationLog.created_at).label("day"),
            func.count(AnnotationLog.id).label("count"),
        )
        .join(Image, Image.id == AnnotationLog.image_id)
        .where(
            Image.dataset_id == dataset_id,
            func.date(AnnotationLog.created_at) >= start,
        )
        .group_by("day")
        .order_by("day")
    )
    rows = (await db.execute(stmt)).all()
    data = {str(row[0]): row[1] for row in rows}

    # 补全缺失日期
    timeline = []
    for i in range(days):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        timeline.append({
            "date": ds,
            "count": data.get(ds, 0),
        })
    return {"days": days, "timeline": timeline}


@router.get("/annotator-efficiency")
async def annotator_efficiency(
    task_type: Optional[str] = Query(default=None, description="按任务类型过滤: classification/detection/segmentation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    标注员效率排行（Top 10）
    - task_type: 可选, 传入时仅统计对应任务类型数据集下的标注记录
                (走 Image -> Dataset 关联, 避免历史脏数据影响)
    """
    stmt = (
        select(
            AnnotationLog.user_id,
            User.username,
            func.count(AnnotationLog.id).label("annos"),
            func.coalesce(func.sum(AnnotationLog.time_spent_ms), 0).label("total_ms"),
        )
        .join(User, User.id == AnnotationLog.user_id)
    )
    if task_type:
        # v2.5.x: 仪表盘按任务类型筛选, 通过 Image -> Dataset.task_type 过滤
        stmt = stmt.join(Image, Image.id == AnnotationLog.image_id).join(
            Dataset, Dataset.id == Image.dataset_id
        ).where(Dataset.task_type == task_type)
    stmt = (
        stmt.group_by(AnnotationLog.user_id, User.username)
        .order_by(func.count(AnnotationLog.id).desc())
        .limit(10)
    )
    rows = (await db.execute(stmt)).all()
    items = []
    for r in rows:
        total_ms = float(r[3] or 0)
        annos = int(r[2] or 0)
        items.append({
            "user_id": int(r[0]),
            "username": r[1],
            "annotation_count": annos,
            "total_seconds": round(total_ms / 1000.0, 2),
            "avg_seconds": round((total_ms / 1000.0) / max(annos, 1), 2),
        })
    return {"items": items}

