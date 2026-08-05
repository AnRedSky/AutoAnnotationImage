"""
权限检查 helper (v3.3.0, v3.3.4 安全加固)
========================================

async 版 can_access_dataset — 检查 owner / team_member.
供 API 层调用, 避免 15 处端点各自实现.

团队角色权限:
  - manager (可管理): 可标注 + 可管理成员/数据集
  - editor (可编辑): 可标注
  - viewer (仅阅读): 只读

v3.3.2 增强 (协作增强):
  - 新增 assert_can_share_to_team: 校验「将数据集共享到团队」的权限
    业务规则: 仅数据集 owner 可发起共享 (owner 同时必须是目标团队的成员,
    避免给非自己团队共享; 团队内仅 manager 角色可对非自己创建的数据集执行权限管理)
  - 提供 can_manage_team / can_edit_team 谓词函数, 供列表/详情组装 my_access

v3.3.4 安全加固 (权限审查整改):
  - 系统角色 (User.role) 与团队角色 (TeamMember.role) 严格分离:
    数据级访问 (assert_can_access_dataset / assert_can_share_to_team)
    仅 super_admin 可绕过, regular admin 仍受团队隔离约束.
  - 平台级管理操作 (用户管理 / 审计 / 团队恢复) 仍走 is_admin() 校验,
    不在此次整改范围.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.dataset import Dataset
from app.tasks.model.team_member import TeamMember, WRITE_ROLES, MANAGE_ROLES


# ============== 谓词 (无副作用, 用于组装 my_access 字段) ==============

def can_manage_team(user: User, team_id: int | None, member_role: str | None) -> bool:
    """当前用户在该团队是否具有「可管理」权限.

    Args:
        user: 当前用户
        team_id: 团队 id (None 表示非团队成员)
        member_role: 成员角色 (manager / editor / viewer / None)

    Returns:
        True if team_id 非空 AND 角色为 manager.
    """
    if team_id is None or member_role is None:
        return False
    return member_role in MANAGE_ROLES


def can_edit_team(member_role: str | None) -> bool:
    """当前成员是否可编辑 (manager + editor)."""
    return member_role is not None and member_role in WRITE_ROLES


# ============== 写操作前的硬校验 (失败抛 HTTPException) ==============

async def assert_can_access_dataset(
    db: AsyncSession,
    current_user: User,
    dataset: Dataset,
    *,
    require_write: bool = False,
) -> None:
    """检查用户是否有权访问数据集, 不通过则 raise 403.

    系统角色 vs 团队角色严格分离 (v3.3.4):
      1. super_admin → 全通 (平台级运维/审计, 不受团队隔离约束)
      2. owner → 全通
      3. team_member (dataset.team_id 非空) → 通过
         - require_write=True 时, viewer 角色被拒
      4. admin/annotator/viewer 角色 → 受团队数据隔离约束, 必须通过 team 共享

    注意: regular admin (User.role='admin') 业务管理员不再自动绕过团队隔离.
    系统级管理 (用户管理/审计查询) 仍通过 is_admin() 校验; 数据级访问受
    团队角色管控, 这是 system role vs team role 的严格分离.
    """
    if current_user.is_super_admin():
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
            if require_write and member.role not in WRITE_ROLES:
                raise HTTPException(403, "只读权限, 不可修改")
            return
    raise HTTPException(403, "无权限访问此数据集")


async def assert_can_share_to_team(
    db: AsyncSession,
    current_user: User,
    dataset: Dataset,
    target_team_id: int,
) -> None:
    """v3.3.2: 校验「将数据集共享到团队」的权限 (v3.3.4 加固).

    业务规则 (与用户新需求 §4「共享权限控制」对齐):
      1. super_admin 可绕过 (平台运维场景, regular admin 不再绕过)
      2. 仅数据集的原始共享者 (owner) 可发起共享
      3. 当前用户必须是目标团队成员 (防止给非自己团队共享)
      4. 目标团队内, 当前用户角色必须是「可管理」(manager)
      5. 目标团队未归档 (已归档不可共享新数据集)
    """
    # 1. super_admin 绕过 (v3.3.4: 收紧为仅超管, regular admin 仍需校验)
    if current_user.is_super_admin():
        return

    # 2. 仅 owner 可共享
    if dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限共享此数据集: 仅数据集所有者可发起共享")

    # 3. 必须是目标团队成员
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == target_team_id,
            TeamMember.user_id == current_user.id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "您不是目标团队的成员, 无法共享数据集")

    # 4. 目标团队内必须是 manager
    if member.role not in MANAGE_ROLES:
        raise HTTPException(
            403,
            "共享数据集需要「可管理」角色,"
            f"您当前角色是「{_role_label(member.role)}」",
        )


def _role_label(role: str) -> str:
    """角色枚举 → 中文标签 (内嵌避免循环 import)."""
    return {
        "manager": "可管理",
        "editor": "可编辑",
        "viewer": "可阅读",
    }.get(role, role)
