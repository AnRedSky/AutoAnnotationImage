"""
ModelVersion 查询函数 (Data Layer) — app/tasks/repository/
========================================================

**v3.0.0 Stage 2.4 迁移**: 从 app/model/model_version_queries.py 迁入 tasks 应用
**v3.0.0 修复**: NULLS LAST 改用 CASE 表达式 (兼容 MySQL/PostgreSQL/SQLite)
**v3.0.0 Phase V #2**: 新增 list_active_per_dataset (单 query + ROW_NUMBER)
"""
from typing import Dict, Optional
from sqlalchemy import case, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.tasks.model.model_version import ModelVersion


async def list_versions_by_dataset(
    db: AsyncSession, dataset_id: int, task_type: Optional[str] = None
) -> list[ModelVersion]:
    """按数据集列出模型版本 (按主指标 desc)

    排序规则: NULL 值排在最后 (按主指标降序, 时间倒序)
    - 分类: accuracy desc, created_at desc
    - 检测: map_50 desc (NULL last), miou desc (NULL last), accuracy desc, created_at desc
    - 分割: miou desc (NULL last), accuracy desc, created_at desc

    兼容性: 使用 `CASE WHEN col IS NULL THEN 1 ELSE 0` 而非 `NULLS LAST`
    原因: MySQL 不支持 NULLS LAST 语法 (PG 专属), CASE 表达式跨方言通用
    """
    stmt = select(ModelVersion).where(ModelVersion.dataset_id == dataset_id)
    if task_type:
        stmt = stmt.where(ModelVersion.task_type == task_type)
    stmt = stmt.order_by(
        # map_50 NULL 排最后: ASC(0=非NULL 优先) + DESC
        case((ModelVersion.map_50.is_(None), 1), else_=0),
        ModelVersion.map_50.desc(),
        # miou NULL 排最后
        case((ModelVersion.miou.is_(None), 1), else_=0),
        ModelVersion.miou.desc(),
        ModelVersion.accuracy.desc(),
        ModelVersion.created_at.desc(),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_active_model(
    db: AsyncSession, dataset_id: int, task_type: str
) -> Optional[ModelVersion]:
    """获取某数据集某任务类型的当前激活模型"""
    versions = await list_versions_by_dataset(db, dataset_id, task_type=task_type)
    for v in versions:
        if v.is_active:
            return v
    if versions:
        return versions[0]
    return None


async def list_active_per_dataset(
    db: AsyncSession, task_type: Optional[str] = None
) -> Dict[int, ModelVersion]:
    """Phase V #2 优化: 全数据集「最优模型」, 单 query + ROW_NUMBER.

    之前路径 (``/api/models/active``): 对每个 dataset 调一次
    list_versions_by_dataset → N+1 query. 对 N=4 dataset 平均 ~63ms.

    新路径: 单 SELECT + PARTITION BY, N=1 query. 预期降到 ~20ms.

    排序与 list_versions_by_dataset 一致: NULLs last + 主指标 desc + created_at desc.
    取每个 dataset_id 的 rn=1 行.

    Args:
        db: 异步 SQLAlchemy session
        task_type: 可选. 限定到 classification/detection/segmentation; None=跨任务.

    Returns:
        dict[dataset_id, ModelVersion]. 不含 dataset_id IS NULL 的项.
    """
    # 子查询: 按 dataset_id partition 内排序
    # Note: ROW_NUMBER 跨数据库方言. SQLAlchemy 用 func.row_number().
    row_num = func.row_number().over(
        partition_by=ModelVersion.dataset_id,
        order_by=(
            case((ModelVersion.map_50.is_(None), 1), else_=0),
            ModelVersion.map_50.desc(),
            case((ModelVersion.miou.is_(None), 1), else_=0),
            ModelVersion.miou.desc(),
            ModelVersion.accuracy.desc(),
            ModelVersion.created_at.desc(),
        ),
    ).label("rn")

    subq = (
        select(ModelVersion, row_num)
        .where(ModelVersion.dataset_id.is_not(None))
    )
    if task_type:
        subq = subq.where(ModelVersion.task_type == task_type)

    subq = subq.subquery()
    # 外层取 rn=1
    stmt = select(subq).where(subq.c.rn == 1)
    result = await db.execute(stmt)
    rows = result.all()

    out: Dict[int, ModelVersion] = {}
    for row in rows:
        # 行解构: subquery 列 + ModelVersion 字段
        # SQLAlchemy 2.x 默认 subquery 返回 row-mapping (c.<col>)
        # ModelVersion 的字段也通过 subq.c.<attr_name> 访问
        ds_id = getattr(row, "dataset_id", None)
        if ds_id is None:
            continue
        # 用单个 ModelVersion 实例
        m = ModelVersion(
            id=getattr(row, "id"),
            name=getattr(row, "name"),
            base_model=getattr(row, "base_model"),
            dataset_id=ds_id,
            task_type=getattr(row, "task_type"),
            num_classes=getattr(row, "num_classes"),
            class_names=getattr(row, "class_names"),
            file_path=getattr(row, "file_path"),
            accuracy=getattr(row, "accuracy") or 0,
            precision=getattr(row, "precision") or 0,
            recall=getattr(row, "recall") or 0,
            f1_score=getattr(row, "f1_score") or 0,
            map_50=getattr(row, "map_50"),
            map_50_95=getattr(row, "map_50_95"),
            miou=getattr(row, "miou"),
            pixel_accuracy=getattr(row, "pixel_accuracy"),
            dice_score=getattr(row, "dice_score"),
            training_log=getattr(row, "training_log"),
            confusion_matrix=getattr(row, "confusion_matrix"),
            is_active=getattr(row, "is_active") or False,
            created_at=getattr(row, "created_at"),
        )
        out[ds_id] = m
    return out


async def get_version_by_id(
    db: AsyncSession, version_id: int
) -> Optional[ModelVersion]:
    return await db.get(ModelVersion, version_id)
