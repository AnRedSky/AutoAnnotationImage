"""
model.activation 模块 — 模型版本激活/取消激活 API
=================================================

**v3.0.0 Phase R 拆分**: 从 model.py 抽离
**职责**: 单个/批量激活 + 单个/批量取消激活

**路由清单** (3 个):
- POST /{model_id}/activate       激活指定模型版本 (单激活语义, 委托 ModelService)
- POST /{model_id}/deactivate     取消激活指定模型版本 (幂等)
- POST /batch-activate            批量激活/取消激活 (active=true|false, 同一事务)

**激活语义** (v2 改造 + v3.0.0 审查强化):
- 单激活不变量: 同 dataset 同一时刻最多 1 个 active 模型
- 委托 ModelService.activate() / deactivate() 保证业务一致性
- 幂等: 目标已是激活/未激活状态时也返回 success=True
- 批量: 同一事务, 全部成功或全部回滚

**S3 行锁约定**:
- batch-activate 使用 `with_for_update()` 锁住全部目标行, 避免并发
  场景下「先查询后更新」的 race condition
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.model_version import ModelVersion
from app.tasks.model.dataset import Dataset
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.service.model_service import ModelService
from app.tasks.service.permission_service import assert_can_access_dataset

logger = logging.getLogger(__name__)
router = APIRouter()


# ============== Schemas ==============

class BatchActivateRequest(BaseModel):
    """批量激活 / 批量取消激活的通用 body"""
    ids: List[int] = Field(..., min_length=1, max_length=200,
                           description="模型版本 id 列表, 数量 1-200")
    active: bool = Field(..., description="True=激活, False=取消激活")


# ============== 单个激活/取消 ==============

@router.post("/{model_id}/activate")
async def activate_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    激活指定模型版本

    v3.0.0 审查修复: 严格遵循 memory 硬约束
    - 同一 dataset 下其他激活模型自动取消 (单激活语义)
    - 委托 ModelService.activate() 保证业务一致性
    - 幂等: 目标已是激活状态时也返回 success=True

    v3.3.0 P0 修复: 必须校验写权限
    """
    target = await db.get(ModelVersion, model_id)
    if not target:
        raise HTTPException(404, "Model not found")

    if target.dataset_id is None:
        raise HTTPException(400, "Model has no associated dataset, cannot activate")

    # 权限校验
    ds = await db.get(Dataset, target.dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    await assert_can_access_dataset(db, current_user, ds, require_write=True)

    try:
        await ModelService.activate(db, target, commit=True)
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"Failed to activate model: {e}")

    return {
        "success": True,
        "active_model_id": model_id,
        "dataset_id": target.dataset_id,
        "is_active": target.is_active,
    }


@router.post("/{model_id}/deactivate")
async def deactivate_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    取消激活指定模型版本
    - 不影响其他模型的状态
    - 幂等: 已经是未激活的也返回 success=True

    v3.0.0 审查修复: 委托 ModelService.deactivate() 保证业务一致性

    v3.3.0 P0 修复: 必须校验写权限
    """
    target = await db.get(ModelVersion, model_id)
    if not target:
        raise HTTPException(404, "Model not found")

    # 权限校验
    if target.dataset_id:
        ds = await db.get(Dataset, target.dataset_id)
        if not ds:
            raise HTTPException(404, "Dataset not found")
        await assert_can_access_dataset(db, current_user, ds, require_write=True)
    elif not current_user.is_super_admin():
        # v3.3.4-PATCH: 收紧为仅 super_admin 可操作孤儿 model, regular admin 仍被拒
        raise HTTPException(403, "无权限操作此模型")

    try:
        await ModelService.deactivate(db, target, commit=True)
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"Failed to deactivate model: {e}")

    return {
        "success": True,
        "deactivated_model_id": model_id,
        "dataset_id": target.dataset_id,
        "is_active": target.is_active,
    }


# ============== 批量激活/取消 ==============

@router.post("/batch-activate")
async def batch_set_active(
    body: BatchActivateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量激活 / 批量取消激活 (active=true|false)
    - 同一事务, 全部成功或全部回滚
    - 不区分 dataset: 用户可一次性"全部激活"或"全部取消激活"
    - 返回: { success, ids, active }

    v3.3.0 P0 修复: 必须校验所有 model 所属 dataset 的写权限
    """
    # 去重保持顺序
    seen: set[int] = set()
    uniq_ids: list[int] = []
    for i in body.ids:
        if i not in seen:
            seen.add(i)
            uniq_ids.append(i)

    rows = (await db.execute(
        select(ModelVersion).where(ModelVersion.id.in_(uniq_ids))
    )).scalars().all()
    found_map = {m.id: m for m in rows}

    missing = [i for i in uniq_ids if i not in found_map]
    if missing:
        raise HTTPException(404, f"模型版本不存在: {missing}")

    # 权限校验: 任一 model 所属 dataset 不可写则整体拒绝
    ds_ids = {m.dataset_id for m in rows if m.dataset_id}
    for ds_id in ds_ids:
        ds = await db.get(Dataset, ds_id)
        if not ds:
            continue
        await assert_can_access_dataset(db, current_user, ds, require_write=True)

    try:
        # 锁全部目标行
        _ = (await db.execute(
            select(ModelVersion).where(ModelVersion.id.in_(uniq_ids)).with_for_update()
        )).scalars().all()
        for m in rows:
            m.is_active = body.active
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error("batch-set-active failed: %s", e)
        raise HTTPException(500, f"批量操作失败: {e}")

    return {
        "success": True,
        "ids": uniq_ids,
        "active": body.active,
    }
