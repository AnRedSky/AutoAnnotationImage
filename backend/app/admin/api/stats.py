"""
Stats API: 标注 / 训练 / 系统综合统计
====================================
为产品运营核心图表提供数据源:
  - 各状态图片分布（饼图）
  - AI 节省时间估算（柱状图）
  - 置信度分布（直方图）
  - 每日标注量（折线图）
  - 训练曲线数据

v3.3.0 P0 修复: 仪表盘数据严格按 user 隔离
- 之前: 全局聚合, 任何登录用户能看到全系统的统计
- 现在: 非 admin 用户只能看到自己有权限访问的数据集相关数据
- 单 dataset 端点: 校验访问权限后再查
- 端点级说明: overview/annotator-efficiency 仅展示当前用户视角的数据
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
from app.tasks.service.permission_service import assert_can_access_dataset

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

    v3.3.0 P0 修复: 严格按用户隔离数据
    - admin: 看全系统数据 (保留原行为, 用于运营监控)
    - 普通用户: 只统计自己有权限访问的数据集 (owner + team_member)
    - 标注数 / 模型版本 / 训练任务: 均按 user 维度过滤
    """
    if current_user.is_admin():
        # 管理员: 保留全局视角
        overview = await StatsService.global_overview(db)
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

    # ---- 非 admin: 仅自己有权限的数据集 ----
    # 1) 收集可见的 dataset_id 列表 (owner 或 team_member)
    from app.tasks.model.team_member import TeamMember
    own_ds_ids_subq = select(Dataset.id).where(Dataset.owner_id == current_user.id)
    team_ds_ids_subq = select(Dataset.id).join(
        TeamMember, TeamMember.team_id == Dataset.team_id
    ).where(TeamMember.user_id == current_user.id)
    # UNION
    visible_ds_ids_stmt = own_ds_ids_subq.union_all(team_ds_ids_subq)
    visible_ds_ids = [r[0] for r in (await db.execute(visible_ds_ids_stmt)).all()]

    if not visible_ds_ids:
        # 无可见数据集, 全部返 0
        return {
            "datasets": 0,
            "images": 0,
            "labeled_images": 0,
            "model_versions": 0,
            "training_jobs": 0,
        }

    # 2) 数据集数
    ds_count = len(visible_ds_ids)

    # 3) 图片总数 + 已标数 (限定到可见数据集)
    img_total = (await db.execute(
        select(func.count(Image.id)).where(Image.dataset_id.in_(visible_ds_ids))
    )).scalar() or 0

    labeled_count = (await db.execute(
        select(func.count(Image.id)).where(
            Image.dataset_id.in_(visible_ds_ids),
            Image.status.in_(("human_confirmed", "human_corrected", "trained")),
        )
    )).scalar() or 0

    # 4) 模型版本数 (限定到可见数据集)
    mv_count = (await db.execute(
        select(func.count(ModelVersion.id)).where(
            ModelVersion.dataset_id.in_(visible_ds_ids)
        )
    )).scalar() or 0

    # 5) 训练任务数 (限定到当前 user)
    job_count = (await db.execute(
        select(func.count(TrainingJob.id)).where(TrainingJob.user_id == current_user.id)
    )).scalar() or 0

    return {
        "datasets": ds_count,
        "images": img_total,
        "labeled_images": labeled_count,
        "model_versions": mv_count,
        "training_jobs": job_count,
    }


@router.get("/dataset/{dataset_id}")
async def dataset_stats(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单数据集统计（产品运营核心图表数据）

    v3.3.0 P0 修复: 访问前必须先校验用户对该数据集有读权限
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 权限校验 (owner / team_member / admin)
    await assert_can_access_dataset(db, current_user, dataset)

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

    v3.3.0 P0 修复: 必须校验数据集访问权限
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    await assert_can_access_dataset(db, current_user, dataset)

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

    v3.3.0 P0 修复: 必须校验数据集访问权限
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    await assert_can_access_dataset(db, current_user, dataset)

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

    v3.3.0 P0 修复: 严格按用户隔离
    - admin: 仍可看全系统排行 (运营视角)
    - 普通用户: 仅返回自己参与的标注统计, 单条 (self only)
      (原本会泄露全公司所有用户的标注量 + 用户名, 严重隐私问题)
    """
    if current_user.is_admin():
        # 管理员: 保留全局视角
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

    # ---- 非 admin: 只返回当前用户自己的统计 ----
    stmt = (
        select(
            func.count(AnnotationLog.id).label("annos"),
            func.coalesce(func.sum(AnnotationLog.time_spent_ms), 0).label("total_ms"),
        ).where(AnnotationLog.user_id == current_user.id)
    )
    if task_type:
        stmt = stmt.join(Image, Image.id == AnnotationLog.image_id).join(
            Dataset, Dataset.id == Image.dataset_id
        ).where(Dataset.task_type == task_type)
    row = (await db.execute(stmt)).one()
    annos = int(row[0] or 0)
    total_ms = float(row[1] or 0)
    return {
        "items": [
            {
                "user_id": current_user.id,
                "username": current_user.username,
                "annotation_count": annos,
                "total_seconds": round(total_ms / 1000.0, 2),
                "avg_seconds": round((total_ms / 1000.0) / max(annos, 1), 2),
            }
        ]
    }
