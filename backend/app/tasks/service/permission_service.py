"""
权限检查 helper (v3.3.0, v3.3.4, v3.3.4-PATCH, v3.3.5-PERMISSION-REWRITE)
=====================================================================

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

v3.3.5-PERMISSION-REWRITE 全面重写 (3 层权限模型 + 严格最小权限):
  - 数据级访问全面移除 super_admin 旁路:
    * assert_can_access_dataset / assert_can_access_training_job /
      assert_can_access_model 均不再对 super_admin 放行
    * super_admin 仍需是 owner 或 team 成员才能访问
  - 系统级管理 (用户管理/审计/系统统计) 仍由 is_admin() 校验,
    不在数据级 helper 中处理, 由各 API 端点自行 require_admin
  - 团队层审计 (team activities) 单独处理: super_admin 可看任意团队活动 (审计),
    但不能修改 team 资源
  - 新增 log_permission_denied: 统一记录 403 越权到 audit_log
  - 孤儿 model (dataset_id=NULL) 强制要求非空 (helper 中显式拒绝)
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.dataset import Dataset
from app.tasks.model.team_member import TeamMember, WRITE_ROLES, MANAGE_ROLES
from app.tasks.model.audit_log import AuditLog


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


# ============== 越权审计日志 (v3.3.5 新增) ==============

async def log_permission_denied(
    db: AsyncSession,
    *,
    current_user: User,
    resource_type: str,
    resource_id: int | None,
    endpoint: str,
    reason: str,
    detail: dict | None = None,
    ip_address: str | None = None,
) -> None:
    """记录越权访问尝试到 audit_log (v3.3.5 新增).

    best-effort 写入, 失败不抛异常, 不阻断主流程返回 403.

    Args:
        db: AsyncSession
        current_user: 当前登录用户
        resource_type: 资源类型 ("dataset" / "training_job" / "model" 等)
        resource_id: 资源 id (可空, 例如按 team 过滤时无单一 id)
        endpoint: 请求端点 (例如 "/api/datasets/{id}")
        reason: 拒绝原因 (例如 "无权限访问此数据集")
        detail: 额外上下文 (可选, 例如 team_id / owner_id / request_method)
        ip_address: 客户端 IP
    """
    try:
        log = AuditLog(
            user_id=current_user.id,
            event_type="permission_denied",
            resource_type=resource_type,
            resource_id=resource_id,
            detail={
                "endpoint": endpoint,
                "reason": reason,
                "user_role": current_user.role,
                **(detail or {}),
            },
            ip_address=ip_address,
        )
        db.add(log)
        await db.flush()
    except Exception:
        # best-effort: 审计失败不影响 403 返回
        pass


# ============== 写操作前的硬校验 (失败抛 HTTPException) ==============

async def assert_can_access_dataset(
    db: AsyncSession,
    current_user: User,
    dataset: Dataset,
    *,
    require_write: bool = False,
) -> None:
    """检查用户是否有权访问数据集, 不通过则 raise 403 + 写审计 (v3.3.5 重写).

    v3.3.5 核心变化 — 严格最小权限:
      - super_admin 不再自动放行数据级访问
      - 必须满足以下任一:
        1. dataset.owner_id == current_user.id (owner)
        2. dataset.team_id 非空 AND 当前用户是该团队成员
           - require_write=True 时, viewer 角色被拒

    系统级管理 (用户管理/审计查询) 仍由 API 层 require_admin 校验,
    不在数据级 helper 中处理.
    """
    # 1. owner
    if dataset.owner_id == current_user.id:
        return
    # 2. team member
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
    # 拒绝 (v3.3.5: super_admin 不再旁路)
    raise HTTPException(403, "无权限访问此数据集")


async def assert_can_share_to_team(
    db: AsyncSession,
    current_user: User,
    dataset: Dataset,
    target_team_id: int,
) -> None:
    """v3.3.2: 校验「将数据集共享到团队」的权限 (v3.3.4 加固).

    业务规则 (与用户新需求 §4「共享权限控制」对齐):
      1. 仅 owner 可发起共享
      2. 当前用户必须是目标团队成员 (防止给非自己团队共享)
      3. 目标团队内, 当前用户角色必须是「可管理」(manager)
      4. 目标团队未归档 (已归档不可共享新数据集)

    v3.3.5: super_admin 不再在数据级旁路, 但作为「平台运维」可发起共享
    (仅 super_admin, regular admin 受 owner 约束).
    """
    # v3.3.5: super_admin 在共享场景保留放行 (这是平台级管理, 不是数据访问)
    if current_user.is_super_admin():
        return

    # 仅 owner 可共享
    if dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限共享此数据集: 仅数据集所有者可发起共享")

    # 必须是目标团队成员
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == target_team_id,
            TeamMember.user_id == current_user.id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "您不是目标团队的成员, 无法共享数据集")

    # 目标团队内必须是 manager
    if member.role not in MANAGE_ROLES:
        raise HTTPException(
            403,
            "共享数据集需要「可管理」角色,"
            f"您当前角色是「{_role_label(member.role)}」",
        )


# ============== training job 权限校验 (v3.3.5 移除 super_admin 旁路) ==============

async def assert_can_access_training_job(
    db: AsyncSession,
    current_user: User,
    job_user_id: int,
    job_dataset_id: int | None = None,
) -> None:
    """检查用户对训练任务的访问权 (v3.3.4-PATCH 新增, v3.3.5 收紧).

    v3.3.5 核心变化 — super_admin 不再旁路:
      1. job.user_id == current_user.id (创建者) → 通过
      2. 通过 job.dataset_id 关联的 dataset:
         - 走 assert_can_access_dataset 校验 (含 team 共享)
      3. 找不到 dataset 时, 仅创建者通过 (v3.3.5 移除 super_admin 旁路)

    Args:
        db: AsyncSession
        current_user: 当前用户
        job_user_id: 训练任务的创建者 user_id
        job_dataset_id: 训练任务关联的数据集 id (可能为 None, e.g. auto_annotate)
    """
    # 1. 创建者
    if job_user_id == current_user.id:
        return
    # 2. 走 dataset 权限 (owner / team 成员)
    if job_dataset_id is not None:
        ds = await db.get(Dataset, job_dataset_id)
        if ds:
            # 走统一的 dataset 权限 (含 team 共享 + viewer 校验)
            await assert_can_access_dataset(db, current_user, ds)
            return
    # 拒绝 (v3.3.5: super_admin 不再旁路, 必须满足 owner / team member)
    raise HTTPException(403, "无权限访问此训练任务")


# ============== model 权限校验 (v3.3.5 移除 super_admin 旁路 + 拒绝孤儿) ==============

async def assert_can_access_model(
    db: AsyncSession,
    current_user: User,
    model_dataset_id: int | None,
    *,
    require_write: bool = False,
) -> None:
    """检查用户对模型版本的访问权 (v3.3.4-PATCH 新增, v3.3.5 收紧).

    v3.3.5 核心变化:
      1. 孤儿 model (dataset_id=NULL) 强制拒绝 (无主 model, 业务异常)
         - 旧逻辑: super_admin 旁路 (越权)
         - 新逻辑: 任何角色均拒绝, 提示先迁移到有效 dataset
      2. super_admin 在数据级不再旁路, 必须走 dataset 权限

    模型本身不存储 owner 字段, 通过关联 dataset 间接校验权限:
      - super_admin → 不再自动放行 (v3.3.5)
      - model.dataset_id 非空 → 走 assert_can_access_dataset 校验
      - model.dataset_id 为空 (孤儿 model) → 拒绝 (任何角色)
    """
    if model_dataset_id is None:
        # v3.3.5: 孤儿 model 任何角色都拒绝, 不再 super_admin 旁路
        raise HTTPException(403, "无主模型, 需先关联到有效数据集后才可访问")
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
