"""
权限检查 helper (v3.3.0)
=========================

async 版 can_access_dataset — 检查 owner / team_member.
供 API 层调用, 避免 15 处端点各自实现.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.dataset import Dataset
from app.tasks.model.team_member import TeamMember


async def assert_can_access_dataset(
    db: AsyncSession,
    current_user: User,
    dataset: Dataset,
    *,
    require_write: bool = False,
) -> None:
    """检查用户是否有权访问数据集, 不通过则 raise 403.

    规则:
      1. admin/super_admin → 全通
      2. owner → 全通
      3. team_member (dataset.team_id 非空) → 通过
         - require_write=True 时, viewer 角色被拒

    用法:
      dataset = await db.get(Dataset, dataset_id)
      if not dataset: raise HTTPException(404, ...)
      await assert_can_access_dataset(db, current_user, dataset)
    """
    if current_user.is_admin():
        return
    if dataset.owner_id == current_user.id:
        return
    if dataset.team_id:
        result = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == dataset.team_id,
                TeamMember.user_id == current_user.id,
            )
        )
        member = result.scalar_one_or_none()
        if member:
            if require_write and member.role == "viewer":
                raise HTTPException(403, "只读权限, 不可修改")
            return
    raise HTTPException(403, "无权限访问此数据集")
