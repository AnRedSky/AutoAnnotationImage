"""
权限检查 helper (v3.3.0, v3.3.4 安全加固, v3.3.4-PATCH 全面修复)
============================================================

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

v3.3.4-PATCH 全面修复 (修复 v3.3.4 遗漏的 list / 单条操作白点):
  - 新增 assert_can_access_training_job / assert_can_access_model 辅助函数
  - 修复 list_datasets / list_training_jobs / list_models / recent_annotations
    等 list 接口的 admin 越权 (is_admin() → is_super_admin() + team 共享过滤)
  - 修复训练任务单条操作 (cancel/pause/resume/delete/error) 的 admin 旁路
  - 修复模型 delete / activate 的 admin 旁路
  - 修复训练日志/进度 (training/detection/segmentation) 的 admin 旁路
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


# ============== v3.3.4-PATCH 新增: training job 权限校验 ==============

async def assert_can_access_training_job(
    db: AsyncSession,
    current_user: User,
    job_user_id: int,
    job_dataset_id: int | None = None,
) -> None:
    """检查用户对训练任务的访问权 (v3.3.4-PATCH 新增).

    校验规则 (与数据集访问对齐):
      1. super_admin → 全通
      2. job.user_id == current_user.id (创建者) → 通过
      3. 通过 job.dataset_id 关联的 dataset:
         - 走 assert_can_access_dataset 校验 (含 team 共享)
      4. 找不到 dataset 时, 仅 super_admin / 创建者通过

    Args:
        db: AsyncSession
        current_user: 当前用户
        job_user_id: 训练任务的创建者 user_id
        job_dataset_id: 训练任务关联的数据集 id (可能为 None, e.g. auto_annotate)
    """
    if current_user.is_super_admin():
        return
    if job_user_id == current_user.id:
        return
    if job_dataset_id is not None:
        ds = await db.get(Dataset, job_dataset_id)
        if ds:
            # 走统一的 dataset 权限 (含 team 共享 + viewer 校验)
            await assert_can_access_dataset(db, current_user, ds)
            return
    raise HTTPException(403, "无权限访问此训练任务")


# ============== v3.3.4-PATCH 新增: model 权限校验 ==============

async def assert_can_access_model(
    db: AsyncSession,
    current_user: User,
    model_dataset_id: int | None,
    *,
    require_write: bool = False,
) -> None:
    """检查用户对模型版本的访问权 (v3.3.4-PATCH 新增).

    模型本身不存储 owner 字段, 通过关联 dataset 间接校验权限:
      1. super_admin → 全通 (含无主 model)
      2. model.dataset_id 非空 → 走 assert_can_access_dataset 校验
      3. model.dataset_id 为空 (孤儿 model) → 仅 super_admin 通过
         (regular admin 不再旁路, 与 v3.3.4 数据级严格分离对齐)

    Args:
        db: AsyncSession
        current_user: 当前用户
        model_dataset_id: 模型关联的数据集 id (可能为 None)
        require_write: 是否需要写权限 (activate/delete 时传 True)
    """
    if current_user.is_super_admin():
        return
    if model_dataset_id is None:
        # 孤儿 model: 仅 super_admin 可访问 (v3.3.4-PATCH 收紧 regular admin)
        raise HTTPException(403, "无主模型, 仅超管可访问")
    ds = await db.get(Dataset, model_dataset_id)
    if not ds:
        raise HTTPException(404, f"模型关联的数据集 (id={model_dataset_id}) 不存在")
    await assert_can_access_dataset(db, current_user, ds, require_write=require_write)


def _role_label(role: str) -> str:
    """角色枚举 → 中文标签 (内嵌避免循环 import)."""
    return {
        "manager": "可管理",
        "editor": "可编辑",
        "viewer": "可阅读",
    }.get(role, role)
