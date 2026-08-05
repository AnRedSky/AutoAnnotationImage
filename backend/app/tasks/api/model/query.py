"""
model.query 模块 — 模型版本查询 API
====================================

**v3.0.0 Phase R 拆分**: 从 model.py 抽离
**职责**: 列表查询 / 详情查询 / 激活模型查询

**路由清单** (3 个):
- GET /                       列出所有模型版本 (支持 dataset_id / active 过滤)
- GET /active                 列出当前激活的模型版本 (每个 dataset 一个最佳模型)
- GET /{model_id}/detail      获取模型详情 (含训练曲线、混淆矩阵、激活状态等)

**字段说明**:
- 列表接口返回的是「轻量级」字段, 不含 training_log / confusion_matrix (太大)
- 详情接口才返回全量字段, 供前端弹窗展示
- 分类指标 (accuracy / precision / recall / f1) + 检测指标 (mAP50/50-95) +
  分割指标 (mIoU / pixel_accuracy / dice) 三套指标按 task_type 透出
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.model_version import ModelVersion
from app.tasks.model.dataset import Dataset
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.service.model_service import ModelService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
@router.get("")  # 同时支持 /api/models 和 /api/models/ (前端曾用 /models 报 404)
async def list_models(
    dataset_id: Optional[int] = Query(default=None, description="按数据集 id 过滤"),
    active: Optional[bool] = Query(default=None, description="按激活状态过滤: true/false"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    列出所有模型版本
    - 可选过滤: dataset_id, active (按激活状态)
    - 同时返回关联 Dataset 的 name, 供前端表格「训练集」列展示

    v3.3.4-PATCH 修复 (admin 越权):
      - P0-3 旧逻辑: is_admin() 看全部, 否则只看自己 dataset 的 model
      - 新逻辑: 仅 super_admin 看全部; regular admin / 普通用户
                看自己创建的 + 团队共享数据集下的 model
      - 过滤条件: model.dataset_id IN (个人所有 + 团队共享) dataset

    v3.3.5-PERMISSION-REWRITE 全面重写:
      - 移除 super_admin 旁路, 任何角色 (含 super_admin) 均按 owner/team 过滤
      - 孤儿 model (dataset_id IS NULL) 不再返回 (需先迁移到有效 dataset)
      - 严格遵循最小权限原则
    """
    from app.tasks.model.team_member import TeamMember as _TM
    from app.tasks.model.team import Team as _Team
    from sqlalchemy import or_ as _or

    # 找出我所在的、未归档的 team_id
    member_team_ids_q = (
        select(_TM.team_id)
        .join(_Team, _Team.id == _TM.team_id)
        .where(
            _TM.user_id == current_user.id,
            _Team.archived_at.is_(None),
        )
    )
    # 数据可见性: 个人所有 + 团队共享
    vis_ds_filter = _or(
        Dataset.owner_id == current_user.id,
        Dataset.team_id.in_(member_team_ids_q),
    )
    visible_ds_subq = select(Dataset.id).where(vis_ds_filter)

    stmt = select(ModelVersion)
    # v3.3.5: 排除孤儿 model (dataset_id IS NULL) — 数据完整性约束
    stmt = stmt.where(ModelVersion.dataset_id.is_not(None))
    if dataset_id is not None:
        stmt = stmt.where(ModelVersion.dataset_id == dataset_id)
    if active is not None:
        stmt = stmt.where(ModelVersion.is_active == active)  # noqa: E712
    # v3.3.5: 任何角色 (含 super_admin) 均按 owner/team 过滤
    stmt = stmt.where(ModelVersion.dataset_id.in_(visible_ds_subq))
    stmt = stmt.order_by(ModelVersion.created_at.desc())
    result = await db.execute(stmt)
    models = result.scalars().all()

    # 预查 dataset_name (兼容没建 relationship 的情况)
    dataset_ids = sorted({m.dataset_id for m in models if m.dataset_id})
    ds_map: dict[int, str] = {}
    if dataset_ids:
        ds_rows = (await db.execute(
            select(Dataset.id, Dataset.name).where(Dataset.id.in_(dataset_ids))
        )).all()
        for row in ds_rows:
            ds_map[row.id] = row.name

    return {
        "items": [
            {
                "id": m.id,
                "name": m.name,
                "base_model": m.base_model,
                # v2.5.24: 前端按 task_type 过滤 finetune models
                # (同一 dataset 可能多 task_type 模型并存, 不加这个字段前端无法分辨)
                "task_type": m.task_type,
                "dataset_id": m.dataset_id,
                "dataset_name": ds_map.get(m.dataset_id) if m.dataset_id else None,
                "num_classes": m.num_classes,
                "accuracy": float(m.accuracy or 0),
                "precision": float(m.precision or 0),
                "recall": float(m.recall or 0),
                "f1_score": float(m.f1_score or 0),
                # 检测专属指标 (m.task_type == "detection" 时非空)
                "map_50": float(m.map_50) if m.map_50 is not None else None,
                "map_50_95": float(m.map_50_95) if m.map_50_95 is not None else None,
                # 分割专属指标 (m.task_type == "segmentation" 时非空)
                "miou": float(m.miou) if m.miou is not None else None,
                "pixel_accuracy": float(m.pixel_accuracy) if m.pixel_accuracy is not None else None,
                "dice_score": float(m.dice_score) if m.dice_score is not None else None,
                "is_active": m.is_active,
                "created_at": m.created_at.isoformat(),
                # v3.0.0: 训练时的类别名称列表 (含 __unqualified__ 时表示已启用不合格检测)
                "class_names": m.class_names,
            }
            for m in models
        ]
    }


@router.get("/active")
async def list_active_models(
    dataset_id: Optional[int] = Query(default=None, description="数据集 ID; 缺省=全局所有数据集, 每个数据集返回 1 个最佳模型"),
    task_type: Optional[str] = Query(default=None, description="任务类型过滤: classification|detection|segmentation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    列出当前激活的模型版本 (供标注工作台 / 概览面板)

    v3.0.0 审查修复: 严格遵循 memory 硬约束
    - 每个 dataset 只返回 1 个最佳模型 (mAP50/mIoU/accuracy 降序, created_at 降序)
    - dataset_id 缺省: 全局所有数据集各自的最优模型
    - 委托 ModelService.get_active_for_dataset() 保证业务一致性

    v3.0.0 Phase V #2: dataset_id 缺省时改用 get_active_for_all_datasets
       (单 query + ROW_NUMBER OVER PARTITION BY). N+1 → 1 query.

    v3.3.0 P0 修复: 严格按用户隔离
    v3.3.4-PATCH 修复 (admin 越权):
      - 旧逻辑: is_admin() 看全系统
      - 新逻辑: 仅 super_admin 看全系统, regular admin 走个人+团队共享过滤

    v3.3.5-PERMISSION-REWRITE 全面重写:
      - 移除 super_admin 旁路, 任何角色均走个人+团队共享过滤
      - 严格遵循最小权限原则
    """
    from app.tasks.model.team_member import TeamMember

    items: list[dict] = []

    # v3.3.5: 移除 super_admin 全局视角, 任何角色均按可见 dataset 过滤
    own_ds_subq = select(Dataset.id).where(Dataset.owner_id == current_user.id)
    team_ds_subq = (
        select(Dataset.id)
        .join(TeamMember, TeamMember.team_id == Dataset.team_id)
        .where(TeamMember.user_id == current_user.id)
    )
    # v3.3.6-STATS-ISOLATION: union 去重, 避免 owner+team member 双计同一 dataset
    visible_ds_subq = own_ds_subq.union(team_ds_subq)

    if dataset_id is not None:
        # 校验该 dataset 可见
        ds = await db.get(Dataset, dataset_id)
        if not ds or ds.id not in [r[0] for r in (await db.execute(visible_ds_subq)).all()]:
            return {"items": [], "count": 0}
        active = await ModelService.get_active_for_dataset(db, dataset_id, task_type=task_type)
        if active:
            items.append(_model_to_dict(active))
    else:
        # 全局路径: 仅遍历可见 dataset
        visible_ids = [r[0] for r in (await db.execute(visible_ds_subq)).all()]
        for ds_id in visible_ids:
            active = await ModelService.get_active_for_dataset(db, ds_id, task_type=task_type)
            if active:
                items.append(_model_to_dict(active))
    return {"items": items, "count": len(items)}


def _model_to_dict(model: ModelVersion) -> dict:
    """统一序列化 (单 dataset 和全 dataset 共用)."""
    return {
        "id": model.id,
        "name": model.name,
        "base_model": model.base_model,
        "dataset_id": model.dataset_id,
        "task_type": model.task_type,
        "num_classes": model.num_classes,
        "accuracy": float(model.accuracy or 0),
        "f1_score": float(model.f1_score or 0),
        "map_50": float(model.map_50) if model.map_50 is not None else None,
        "miou": float(model.miou) if model.miou is not None else None,
        "is_active": model.is_active,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "best_epoch": None,  # 暂无字段; 占位
    }


@router.get("/{model_id}/detail")
async def get_model_detail(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取模型详情 (含训练曲线、混淆矩阵、激活状态等)

    返回字段与列表接口 (/api/models) 保持一致, 避免前端弹窗显示与列表
    不一致 (例如激活状态: 列表显示「已激活」但弹窗显示「未激活」).

    v3.3.0 P0 修复: 必须校验访问权限
    v3.3.5-PERMISSION-REWRITE: 孤儿 model 任何角色均拒绝
      (含 super_admin, 不再旁路)
    """
    from app.tasks.service.permission_service import assert_can_access_model
    m = await db.get(ModelVersion, model_id)
    if not m:
        raise HTTPException(404, "Model not found")

    # v3.3.5: 统一通过 assert_can_access_model 校验, 孤儿 model 任何角色拒绝
    await assert_can_access_model(db, current_user, m.dataset_id)

    # 同步返回 dataset_name (前端弹窗可能用)
    ds_name = None
    if m.dataset_id:
        d = await db.get(Dataset, m.dataset_id)
        if d:
            ds_name = d.name

    return {
        "id": m.id,
        "name": m.name,
        "base_model": m.base_model,
        "dataset_id": m.dataset_id,
        "dataset_name": ds_name,
        # v2.5.16: 任务类型透出 (与 list_models 对齐)
        "task_type": m.task_type,
        "num_classes": m.num_classes,
        "accuracy": float(m.accuracy or 0),
        "precision": float(m.precision or 0),
        "recall": float(m.recall or 0),
        "f1_score": float(m.f1_score or 0),
        # 检测专属指标
        "map_50": float(m.map_50) if m.map_50 is not None else None,
        "map_50_95": float(m.map_50_95) if m.map_50_95 is not None else None,
        # 分割专属指标
        "miou": float(m.miou) if m.miou is not None else None,
        "pixel_accuracy": float(m.pixel_accuracy) if m.pixel_accuracy is not None else None,
        "dice_score": float(m.dice_score) if m.dice_score is not None else None,
        "is_active": m.is_active,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "training_log": m.training_log,
        "confusion_matrix": m.confusion_matrix,
        "file_path": m.file_path,
        # v3.0.0: 训练时的类别名称列表 (含 __unqualified__ 时表示已启用不合格检测)
        "class_names": m.class_names,
    }
