"""
image.query 模块 — 图片查询 API
================================

v3.0.0 Phase N 拆分: 从 image.py 抽离
- 职责: 列表查询 (分页) + 单图详情
- 包含 v2.5.17 修复: 批量补齐 detection/segmentation 的"实际标注数"
  (bbox_count / has_mask 字段供前端「去标」按钮使用)

v3.5.0 新增: 轻量级 list_ids 接口
- 解决标注工作台批量操作时被 100 张限制的问题
- 仅返回 image id 数组 + total, 避免大量 join/序列化
- 上限 max_ids (默认 2000) 防止极端数据集返回过大

v3.6.1 修复: status 参数支持多状态 IN 查询
- 背景: 标注工作台「已人工标注」状态卡聚合显示 human_confirmed + human_corrected
- 旧实现只支持 Image.status == status 单值, 传 "human_confirmed,human_corrected" 永远不匹配
- 新实现: status 含逗号时, 走 Image.status.in_([...])
- 'unqualified' 仍走 quality_flag (正交维度), 不参与 IN
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.category import Category
from app.tasks.model.annotation_log import AnnotationLog
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user

# query 独立 router
router = APIRouter()


def _apply_status_filter(stmt: Select, status: Optional[str]) -> Select:
    """根据 status 参数为 SQL 加上过滤条件

    行为:
    - 空/None: 不过滤
    - 包含 'unqualified': 走 quality_flag 维度 (正交于 status)
    - 单个值: Image.status == status, 同时排除不合格图
    - 多个值 (逗号分隔): Image.status.in_([...]), 同时排除不合格图

    v3.6.1 新增: 多状态 IN 查询, 支持前端 "已人工标注" = human_confirmed + human_corrected
    """
    if not status:
        return stmt
    parts = [s.strip() for s in status.split(",") if s.strip()]
    if not parts:
        return stmt
    if "unqualified" in parts:
        # quality_flag 是正交维度, 不能与其他 status 混用
        # 若同时存在, 仅取第一个 unqualified, 其它忽略
        return stmt.where(Image.quality_flag == "unqualified")
    if len(parts) == 1:
        stmt = stmt.where(Image.status == parts[0])
    else:
        stmt = stmt.where(Image.status.in_(parts))
    # 排除不合格图 (与 list 语义保持一致, 避免 workbench 加载到不合格图)
    stmt = stmt.where(Image.quality_flag.is_(None))
    return stmt


# v3.5.0: list_ids 接口的最大返回数量 (防止一次性返回过多 ID 导致前端渲染卡顿)
LIST_IDS_MAX = 2000


@router.get("/list/{dataset_id}")
async def list_images(
    dataset_id: int,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    exclude_id: Optional[int] = Query(default=None, description="排除的 image id (标注工作台「下一张」用, 避免连续返回同一张)"),
    exclude_ids: Optional[str] = Query(default=None, description="批量排除的 image id 列表, 逗号分隔, 用于排除「本会话已加载但未标注」的全部图片, 防止连续点下一张回到已看过的图"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    分页查询数据集下的图片
    新增 total / status / file_size / file_url 字段, 便于前端做图像网格

    exclude_id: 排除单个 image id (单张维度, 兼容旧调用)
    exclude_ids: 批量排除, 逗号分隔, 例如 "1,2,3" (标注工作台「下一张」累积已看过的图, 避免循环回到已看过的)
    """
    # 校验数据集
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset)

    # 类别映射: id -> name
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    cat_map = {c.id: c.name for c in cat_rows}

    # base 查询
    base = select(Image).where(Image.dataset_id == dataset_id)
    # v3.0.0: status="unqualified" 是正交维度, 转查 quality_flag
    # 其它 status 值正常按 Image.status 过滤, 并额外排除不合格图片
    # (标注工作台 loadNext 不应返回不合格图)
    # v3.0.0 加固: "待标注" (status=pending) 严格不包含已处理的图
    # - 不合格图: quality_flag != null → 排除
    # - 人工确认/修正图: status 升级到 human_confirmed/corrected → 已被 status 过滤排除
    # - 这保证了"待标注队列"中每张图都是真正未处理过的, 防止重复标注
    # v3.6.1: status 支持多状态 (e.g. "human_confirmed,human_corrected")
    base = _apply_status_filter(base, status)

    # 合并 exclude_id + exclude_ids, 统一用 NOT IN
    exclude_set: set = set()
    if exclude_ids:
        for x in exclude_ids.split(','):
            x = x.strip()
            if x.isdigit():
                exclude_set.add(int(x))
    if exclude_id is not None:
        exclude_set.add(exclude_id)
    if exclude_set:
        base = base.where(Image.id.notin_(exclude_set))

    # total
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页
    stmt = base.order_by(Image.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    images = result.scalars().all()

    # v2.5.17: 批量补齐 detection/segmentation 的"实际标注数"
    img_ids = [img.id for img in images]
    bbox_count_by_img: dict = {}
    has_mask_by_img: dict = {}
    if img_ids:
        ds_id = dataset_id
        from app.annotation.model.bbox_annotation import BBoxAnnotation
        from app.annotation.model.segmentation_mask import SegmentationMask
        det_ids_subq = select(Image.id).where(
            Image.dataset_id == ds_id,
            Image.id.in_(img_ids),
            Image.task_type == "detection",
        )
        seg_ids_subq = select(Image.id).where(
            Image.dataset_id == ds_id,
            Image.id.in_(img_ids),
            Image.task_type == "segmentation",
        )
        r = await db.execute(
            select(BBoxAnnotation.image_id, func.count(BBoxAnnotation.id))
            .where(BBoxAnnotation.image_id.in_(det_ids_subq))
            .group_by(BBoxAnnotation.image_id)
        )
        bbox_count_by_img = {row[0]: int(row[1]) for row in r.all()}
        r = await db.execute(
            select(SegmentationMask.image_id)
            .where(SegmentationMask.image_id.in_(seg_ids_subq))
        )
        has_mask_by_img = {row[0]: True for row in r.all()}

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "status_filter": status,
        "exclude_id": exclude_id,
        "exclude_ids": sorted(exclude_set) if exclude_set else None,
        "items": [
            {
                "id": img.id,
                "dataset_id": img.dataset_id,
                "filename": img.filename,
                "status": img.status,
                "task_type": img.task_type,
                "width": img.width,
                "height": img.height,
                "file_size": img.file_size,
                "file_hash": img.file_hash,
                "ai_prediction": img.ai_prediction,
                "final_label_id": img.final_label_id,
                "final_label_name": cat_map.get(img.final_label_id) if img.final_label_id else None,
                "annotated_by": img.annotated_by,
                "annotated_at": img.annotated_at.isoformat() if img.annotated_at else None,
                "created_at": img.created_at.isoformat() if img.created_at else None,
                "file_url": f"/api/files/{img.id}",
                "bbox_count": bbox_count_by_img.get(img.id, 0),
                "has_mask": has_mask_by_img.get(img.id, False),
                # v3.0.0: 不合格标记 (正交于 status)
                "quality_flag": img.quality_flag,
                "reject_reason": img.reject_reason,
            }
            for img in images
        ],
    }


@router.get("/{image_id}")
async def get_image_detail(
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单图详情 (供 DatasetDetail/标注查看器使用)
    返回图片元数据 + AI 预测 + 最终类别 + 标注日志

    v3.3.0 P0 修复: 必须校验访问权限
    - 之前: 任何登录用户可按 ID 拿到任何图片的元数据 + 标注历史
    - 现在: 必须对该 image 所属 dataset 有读权限
    """
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")

    # 权限校验
    dataset = await db.get(Dataset, img.dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset)

    # 类别
    final_label = None
    if img.final_label_id:
        c = await db.get(Category, img.final_label_id)
        if c:
            final_label = {"id": c.id, "name": c.name, "color": c.color}

    # 标注日志
    log_rows = (await db.execute(
        select(AnnotationLog)
        .where(AnnotationLog.image_id == image_id)
        .order_by(AnnotationLog.created_at.desc())
        .limit(20)
    )).scalars().all()

    # 取所有相关类别 (供 Top-5 比对)
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == img.dataset_id)
    )).scalars().all()
    cat_map = {c.name.lower(): c.id for c in cat_rows}

    logs = []
    for log in log_rows:
        from_lab = None
        to_lab = None
        if log.from_label_id:
            fc = await db.get(Category, log.from_label_id)
            if fc:
                from_lab = {"id": fc.id, "name": fc.name}
        if log.to_label_id:
            tc = await db.get(Category, log.to_label_id)
            if tc:
                to_lab = {"id": tc.id, "name": tc.name}
        logs.append({
            "id": log.id,
            "action": log.action,
            "from_label": from_lab,
            "to_label": to_lab,
            "time_spent_ms": log.time_spent_ms,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            # v3.0.0: 返回 payload 供前端展示扩展信息
            # - mark_unqualified: {"reason": "blurry", "custom_text": "..."}
            # - 其他 action: 通常为 None
            "payload": log.payload,
        })

    return {
        "id": img.id,
        "dataset_id": img.dataset_id,
        "filename": img.filename,
        "status": img.status,
        "task_type": img.task_type,
        "width": img.width,
        "height": img.height,
        "file_size": img.file_size,
        "file_hash": img.file_hash,
        "ai_prediction": img.ai_prediction,
        "final_label": final_label,
        "annotated_by": img.annotated_by,
        "annotated_at": img.annotated_at.isoformat() if img.annotated_at else None,
        "created_at": img.created_at.isoformat() if img.created_at else None,
        "file_url": f"/api/files/{img.id}",
        "annotation_history": logs,
        # v3.0.0: 不合格标记字段 (正交于 status 状态机)
        # - 修复: 上一张/下一张切回时丢失不合格状态的问题
        # - 与 list_images 接口的字段保持一致
        "quality_flag": img.quality_flag,
        "reject_reason": img.reject_reason,
        "rejected_by": img.rejected_by,
        "rejected_at": img.rejected_at.isoformat() if img.rejected_at else None,
    }


@router.get("/ids/{dataset_id}")
async def list_image_ids(
    dataset_id: int,
    status: Optional[str] = Query(
        default=None,
        description="按 status 过滤 (与 /list 语义一致: 'unqualified' 走 quality_flag, 其它按 Image.status, 并排除不合格)",
    ),
    order: str = Query(
        default="desc",
        description="排序方向: 'asc' (id 升序, 最旧优先) / 'desc' (id 降序, 最新优先, 与 /list 默认一致)",
    ),
    max_ids: int = Query(
        default=LIST_IDS_MAX,
        ge=1,
        le=LIST_IDS_MAX,
        description=f"最多返回的 id 数量, 上限 {LIST_IDS_MAX}",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    v3.5.0 新增: 轻量级 id 列表接口

    背景:
      - 旧 /list 接口 page_size 上限通常 100, 无法支撑批量操作
      - 工作台批量操作 (全选 / 批量清除 / 批量标记不合格) 需要完整的 id 集合
      - 完整字段 (filename/width/ai_prediction...) 对批量操作无价值, 仅 ID + total 即可

    语义对齐 list_images:
      - status='unqualified' → 查 quality_flag='unqualified'
      - 其它 status 值 → 查 Image.status, 同时排除不合格图
      - 排序默认 desc (最新优先), 与 /list 一致, 保持用户视觉习惯

    性能优化:
      - 仅 select(Image.id), 不查其他列, 避免 ORM 加载完整 Image 对象
      - 一次返回 total, 前端可直接展示"共 N 张"标签
      - max_ids 兜底, 防止极端数据集 (10w+ 图) 一次性返回过大导致前端卡顿
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset)

    base = select(Image.id).where(Image.dataset_id == dataset_id)
    # v3.6.1: 与 list_images 保持一致, 支持多状态 IN 查询
    base = _apply_status_filter(base, status)

    # total (在 limit 之前, 与 /list 语义一致: total 是真实总数, 不是被 max_ids 截断的数)
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 排序 + 截断
    order_col = Image.id.desc() if order == "desc" else Image.id.asc()
    stmt = base.order_by(order_col).limit(max_ids)
    rows = (await db.execute(stmt)).scalars().all()
    items = [int(x) for x in rows]
    truncated = total > len(items)

    return {
        "items": items,
        "total": total,
        "max_ids": max_ids,
        "order": order,
        "truncated": truncated,
        "status_filter": status,
    }
