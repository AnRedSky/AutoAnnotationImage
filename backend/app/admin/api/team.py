"""
Team Management API (v3.3.0 + v3.3.1 增强)
============================================

团队管理端点 (功能入口公开 + 数据严格隔离):
  - GET    /api/teams                       列出我创建和加入的团队 (数据隔离)
  - POST   /api/teams                       创建团队 (任何已登录用户)
  - GET    /api/teams/{id}                  团队详情 (仅成员可见)
  - PATCH  /api/teams/{id}                  编辑团队 (v3.3.1, manager/owner)
  - DELETE /api/teams/{id}                  删除团队 (仅创建者)
  - GET    /api/teams/{id}/datasets         团队级数据集列表 (v3.3.1, 任意成员)
  - GET    /api/teams/{id}/members          团队成员列表 (仅成员可见)
  - POST   /api/teams/{id}/members          邀请成员 (仅 manager/owner)
  - PUT    /api/teams/{id}/members/{uid}    修改成员角色 (仅 manager/owner)
  - DELETE /api/teams/{id}/members/{uid}    移除成员 (仅 manager/owner)
  - DELETE /api/teams/{id}/members/me       主动退队 (v3.3.1, 任意成员, owner 例外)
  - POST   /api/teams/{id}/transfer         转让所有权 (v3.3.1, 仅 owner)
  - POST   /api/datasets/{id}/share         把数据集共享给团队 (仅数据集 owner)
  - DELETE /api/datasets/{id}/share         取消共享 (仅数据集 owner)

数据隔离规则:
  - 用户仅可见自己创建或已加入的团队
  - 非团队成员 (包括系统管理员) 不可访问团队信息
  - 团队间数据完全独立

角色:
  - manager (可管理): 管理成员 + 配置数据集权限 + 编辑标注
  - editor (可编辑): 可对共享数据集进行标注
  - viewer (仅阅读): 只读

v3.3.1 增强:
  - PATCH 编辑端点 + 配额降级校验
  - 转让所有权端点 + 二次确认
  - 主动退队端点
  - 团队级数据集列表端点
  - 11 个写操作全部加审计日志 (log_audit)
  - 团队删除时 (Phase L1 仍是硬删), 数据集 team_id 清空
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember, TEAM_ROLES, WRITE_ROLES
from app.tasks.model.dataset import Dataset
from app.middleware.http.auth import get_current_user
from app.tasks.service.audit_service import log_audit

router = APIRouter()


# ============== Schemas ==============

class TeamCreate(BaseModel):
    name: str
    slug: Optional[str] = None  # 可选, 为空时从 name 自动生成
    description: Optional[str] = None
    max_members: int = 20


class TeamUpdate(BaseModel):
    """v3.3.1: 团队编辑请求体 (所有字段可选)."""
    name: Optional[str] = None
    description: Optional[str] = None
    max_members: Optional[int] = Field(None, ge=2, le=100)


class MemberInvite(BaseModel):
    user_id: int
    role: str = "editor"  # manager / editor / viewer


class MemberRoleUpdate(BaseModel):
    role: str  # manager / editor / viewer


class TeamTransfer(BaseModel):
    """v3.3.1: 团队所有权转让请求体."""
    new_owner_id: int
    confirm: bool = False  # 二次确认 (前端必须让用户输入团队名或勾选确认)


# ============== Helper: 权限检查 ==============

def _generate_slug_from_name(name: str) -> str:
    """从团队名称自动生成 URL 友好的 slug.

    规则: 小写 → 保留字母数字/中文 → 空格转连字符 → 合并连续连字符 → 去首尾连字符 → 截断 50 字符.
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\u4e00-\u9fa5\s-]", "", slug)  # 去特殊字符, 保留中文
    slug = re.sub(r"[\s_]+", "-", slug)                  # 空格/下划线转连字符
    slug = re.sub(r"-+", "-", slug)                       # 合并连续连字符
    slug = slug.strip("-")                                # 去首尾连字符
    return slug[:50] or "team"                            # 截断 50 字符, 空则回退 "team"


async def _ensure_unique_slug(db: AsyncSession, slug: str) -> str:
    """确保 slug 唯一: 冲突时自动追加数字后缀 (my-team → my-team-2 → my-team-3)."""
    base_slug = slug
    suffix = 1
    while True:
        result = await db.execute(select(Team).where(Team.slug == slug))
        if not result.scalar_one_or_none():
            return slug
        suffix += 1
        slug = f"{base_slug}-{suffix}"


async def _get_member_or_403(db: AsyncSession, team_id: int, user_id: int) -> TeamMember:
    """查 TeamMember, 不存在则 403 (数据隔离: 非成员不可访问)."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "无权限: 非团队成员")
    return member


async def _assert_can_manage(db: AsyncSession, team: Team, user: User) -> TeamMember:
    """要求可管理权限 (manager) 或 owner.

    注意: 不再对 admin 绕过 — 非团队成员(包括管理员)不可操作.
    """
    if team.owner_id == user.id:
        # owner 自动有 manager 权限
        # 但仍需返回 member 对象 (owner 可能不在 team_member 表里)
        result = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.user_id == user.id,
            )
        )
        return result.scalar_one_or_none() or TeamMember(
            team_id=team.id, user_id=user.id, role="manager"
        )
    member = await _get_member_or_403(db, team.id, user.id)
    if not member.can_manage():
        raise HTTPException(403, "无权限: 需要「可管理」角色")
    return member


async def _count_managers(db: AsyncSession, team_id: int) -> int:
    """统计团队中 manager 数量 (用于 L2 阶段: 最后一名 manager 保护)."""
    result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team_id,
            TeamMember.role == "manager",
        )
    )
    return result.scalar() or 0


# ============== Team CRUD ==============

@router.get("")
async def list_my_teams(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出我创建和加入的团队 (数据隔离: 仅可见自己所属的团队)."""
    result = await db.execute(
        select(Team, TeamMember.role)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == current_user.id)
        .order_by(Team.id)
    )
    rows = result.all()
    return {
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "description": t.description,
                "owner_id": t.owner_id,
                "max_members": t.max_members,
                "my_role": role,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t, role in rows
        ]
    }


@router.post("")
async def create_team(
    body: TeamCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建团队 (任何已登录用户可创建, 创建者自动成为 manager).

    slug 处理:
      1. 请求未提供 slug → 从 name 自动生成
      2. slug 冲突 → 自动追加数字后缀 (my-team → my-team-2 → my-team-3)
    避免用户首次创建团队时遇到"短标识重复"错误.
    """
    # slug 生成 + 唯一性处理
    slug = body.slug.strip() if body.slug else ""
    if not slug:
        slug = _generate_slug_from_name(body.name)
    slug = await _ensure_unique_slug(db, slug)

    team = Team(
        name=body.name,
        slug=slug,
        description=body.description,
        owner_id=current_user.id,
        max_members=body.max_members,
    )
    db.add(team)
    await db.flush()

    # 创建者自动加入为 manager
    member = TeamMember(
        team_id=team.id,
        user_id=current_user.id,
        role="manager",
        invited_by_id=None,  # 创建者无需邀请人
    )
    db.add(member)

    # v3.3.1: 审计
    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_created",
        team_id=team.id,
        resource_type="team",
        resource_id=team.id,
        detail={"name": team.name, "slug": team.slug, "max_members": team.max_members},
    )
    await db.commit()
    await db.refresh(team)
    return {"id": team.id, "name": team.name, "slug": team.slug}


@router.get("/{team_id}")
async def get_team(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """团队详情 (仅成员可见, 数据隔离)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    # 数据隔离: 非成员不可访问 (包括管理员)
    await _get_member_or_403(db, team_id, current_user.id)

    return {
        "id": team.id,
        "name": team.name,
        "slug": team.slug,
        "description": team.description,
        "owner_id": team.owner_id,
        "max_members": team.max_members,
        "created_at": team.created_at.isoformat() if team.created_at else None,
    }


@router.patch("/{team_id}")
async def update_team(
    team_id: int,
    body: TeamUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """v3.3.1: 编辑团队 (manager/owner 可用).

    业务规则:
      1. 至少一个非空字段
      2. max_members 范围 [2, 100]
      3. max_members 降级时不能 < 当前成员数
      4. 任意字段变更写入 audit_log
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    await _assert_can_manage(db, team, current_user)

    # 至少一个字段
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(400, "至少提供一个可编辑字段")

    # 配额降级校验
    if "max_members" in update_data:
        new_cap = update_data["max_members"]
        count_result = await db.execute(
            select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id)
        )
        current_count = count_result.scalar() or 0
        if new_cap < current_count:
            raise HTTPException(
                400, f"新成员上限 {new_cap} 不能小于当前成员数 {current_count}"
            )

    # 计算 diff (用于审计)
    changes = {}
    for field, new_val in update_data.items():
        old_val = getattr(team, field)
        if old_val != new_val:
            changes[field] = {"old": old_val, "new": new_val}
            setattr(team, field, new_val)

    if changes:
        await log_audit(
            db,
            user_id=current_user.id,
            event_type="team_updated",
            team_id=team_id,
            resource_type="team",
            resource_id=team_id,
            detail={"changes": changes},
        )
        await db.commit()
        await db.refresh(team)

    return {
        "id": team.id,
        "name": team.name,
        "slug": team.slug,
        "description": team.description,
        "max_members": team.max_members,
    }


@router.delete("/{team_id}")
async def delete_team(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除团队 (仅创建者).

    v3.3.1 行为: 硬删 (Phase L3 软删). 级联删除:
      - team_member (ON DELETE CASCADE)
      - 数据集 team_id 字段被清空 (下方手动处理)
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if team.owner_id != current_user.id:
        raise HTTPException(403, "仅创建者可删除团队")

    # 统计成员数 (审计)
    member_count = (await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id)
    )).scalar() or 0

    # 清空关联数据集的 team_id (避免悬挂引用)
    await db.execute(
        Dataset.__table__.update()
        .where(Dataset.team_id == team_id)
        .values(team_id=None)
    )

    # 审计 (在 db.delete 前记录, 避免级联删除时 audit_log 也被影响)
    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_deleted",
        team_id=team_id,
        resource_type="team",
        resource_id=team_id,
        detail={"name": team.name, "member_count": member_count},
    )

    await db.delete(team)
    await db.commit()
    return {"success": True, "detail": f"Team {team_id} deleted"}


# ============== 成员管理 ==============

@router.get("/{team_id}/members")
async def list_members(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """团队成员列表 (仅成员可见, 数据隔离)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    # 数据隔离: 非成员不可访问
    await _get_member_or_403(db, team_id, current_user.id)

    result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.joined_at)
    )
    rows = result.all()
    return {
        "items": [
            {
                "user_id": r[1].id,
                "username": r[1].username,
                "email": r[1].email,
                "role": r[0].role,
                "joined_at": r[0].joined_at.isoformat() if r[0].joined_at else None,
            }
            for r in rows
        ]
    }


@router.post("/{team_id}/members")
async def invite_member(
    team_id: int,
    body: MemberInvite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """邀请成员 (仅 manager/owner)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    await _assert_can_manage(db, team, current_user)

    if body.role not in TEAM_ROLES:
        raise HTTPException(400, f"无效角色: {body.role}, 可选: {TEAM_ROLES}")

    user = await db.get(User, body.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    # 幂等
    existing = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == body.user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "用户已在团队中")

    # 配额
    count_result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id)
    )
    if len(count_result.scalars().all()) >= team.max_members:
        raise HTTPException(400, f"团队已满 (上限 {team.max_members})")

    member = TeamMember(
        team_id=team_id,
        user_id=body.user_id,
        role=body.role,
        invited_by_id=current_user.id,  # v3.3.1: 邀请溯源
    )
    db.add(member)

    # v3.3.1: 审计
    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_member_invited",
        team_id=team_id,
        resource_type="team_member",
        resource_id=body.user_id,
        detail={
            "invitee_id": body.user_id,
            "invitee_username": user.username,
            "role": body.role,
            "invited_by": current_user.id,
        },
    )
    await db.commit()
    return {"team_id": team_id, "user_id": body.user_id, "role": body.role}


@router.delete("/{team_id}/members/me")
async def leave_team(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """v3.3.1: 主动退出团队 (任意成员, owner 需先转让).

    业务规则:
      - owner 必须先转让所有权, 否则 400
      - 删除自己的 TeamMember 记录
      - 写入 team_left 审计

    注意: 此路由必须定义在 DELETE /members/{user_id} 之前,
    否则 FastAPI 会把 "me" 当作 user_id 参数.
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if team.owner_id == current_user.id:
        raise HTTPException(400, "创建者需先转让所有权才能退出团队")

    member = await _get_member_or_403(db, team_id, current_user.id)

    await db.delete(member)
    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_left",
        team_id=team_id,
        resource_type="team_member",
        resource_id=current_user.id,
        detail={"user_id": current_user.id, "old_role": member.role},
    )
    await db.commit()
    return {"success": True, "detail": "已退出团队"}


@router.put("/{team_id}/members/{user_id}")
async def update_member_role(
    team_id: int,
    user_id: int,
    body: MemberRoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改成员角色 (仅 manager/owner)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    await _assert_can_manage(db, team, current_user)

    if body.role not in TEAM_ROLES:
        raise HTTPException(400, f"无效角色: {body.role}")

    # owner 不能被降级
    if user_id == team.owner_id and body.role != "manager":
        raise HTTPException(400, "创建者必须保持「可管理」角色")

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "成员不存在")

    old_role = member.role
    if old_role == body.role:
        return {"team_id": team_id, "user_id": user_id, "role": member.role}

    member.role = body.role

    # v3.3.1: 审计
    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_member_role_changed",
        team_id=team_id,
        resource_type="team_member",
        resource_id=user_id,
        detail={"old_role": old_role, "new_role": body.role},
    )
    await db.commit()
    return {"team_id": team_id, "user_id": user_id, "role": member.role}


@router.delete("/{team_id}/members/{user_id}")
async def remove_member(
    team_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """移除成员 (仅 manager/owner; owner 不能被移除)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    await _assert_can_manage(db, team, current_user)

    if user_id == team.owner_id:
        raise HTTPException(400, "不能移除团队创建者")

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "成员不存在")

    await db.delete(member)

    # v3.3.1: 审计
    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_member_removed",
        team_id=team_id,
        resource_type="team_member",
        resource_id=user_id,
        detail={"removed_id": user_id, "old_role": member.role},
    )
    await db.commit()
    return {"success": True, "detail": f"用户 {user_id} 已从团队 {team_id} 移除"}


# ============== 所有权转让 (v3.3.1) ==============

@router.post("/{team_id}/transfer")
async def transfer_ownership(
    team_id: int,
    body: TeamTransfer,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """v3.3.1: 转让所有权 (仅 owner, 接收方必须是 manager/editor).

    业务规则:
      1. 仅 owner 可发起
      2. 接收方必须是本团队现有 member
      3. 接收方角色不能是 viewer
      4. 接收方角色强制提升为 manager
      5. confirm=False 时返回 400 (防误操作)
      6. 写入 team_ownership_transferred 审计
    """
    if not body.confirm:
        raise HTTPException(400, "请确认转让操作 (设置 confirm=true)")

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if team.owner_id != current_user.id:
        raise HTTPException(403, "仅创建者可转让所有权")

    # 校验接收方
    new_owner_member = await _get_member_or_403(db, team_id, body.new_owner_id)
    if new_owner_member.role == "viewer":
        raise HTTPException(400, "不能转让给「仅阅读」角色")

    # 接收方不能是自己
    if body.new_owner_id == current_user.id:
        raise HTTPException(400, "不能转让给自己")

    old_owner_id = team.owner_id
    team.owner_id = body.new_owner_id
    new_owner_member.role = "manager"  # 强制提升

    # 原 owner 若仍在 member 表, 保持 manager 角色 (避免权限真空)
    if old_owner_id != body.new_owner_id:
        old_owner_member_result = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == old_owner_id,
            )
        )
        old_owner_member = old_owner_member_result.scalar_one_or_none()
        if old_owner_member and old_owner_member.role != "manager":
            old_owner_member.role = "manager"

    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_ownership_transferred",
        team_id=team_id,
        resource_type="team",
        resource_id=team_id,
        detail={"old_owner": old_owner_id, "new_owner": body.new_owner_id},
    )
    await db.commit()
    return {
        "success": True,
        "old_owner_id": old_owner_id,
        "new_owner_id": body.new_owner_id,
    }


# ============== 团队级数据集 (v3.3.1) ==============

@router.get("/{team_id}/datasets")
async def list_team_datasets(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """v3.3.1: 列出共享给本团队的数据集 (任意成员可见).

    返回字段: id, name, description, task_type, image_count, annotated_count,
    category_count, status, owner_id, owner_name, my_access.
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    # 数据隔离: 非成员不可访问
    member = await _get_member_or_403(db, team_id, current_user.id)

    # 拉团队数据集 + 一次 JOIN 拿 owner_name
    result = await db.execute(
        select(Dataset, User.username)
        .join(User, User.id == Dataset.owner_id)
        .where(Dataset.team_id == team_id)
        .order_by(Dataset.id.desc())
    )
    rows = result.all()

    return {
        "items": [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "task_type": d.task_type,
                "image_count": d.image_count,
                "annotated_count": d.annotated_count,
                "category_count": d.category_count,
                "status": d.status,
                "owner_id": d.owner_id,
                "owner_name": owner_name,
                # my_access: 当前成员在团队中的角色
                "my_access": member.role,
            }
            for d, owner_name in rows
        ]
    }


# ============== 数据集共享 ==============

@router.post("/datasets/{dataset_id}/share")
async def share_dataset_to_team(
    dataset_id: int,
    team_id: int = Query(..., description="目标团队 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """把数据集共享给团队 (仅数据集 owner)."""
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限共享此数据集")

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")

    # v3.3.1: 共享前必须要求当前用户是团队成员 (避免给非自己团队共享)
    await _get_member_or_403(db, team_id, current_user.id)

    old_team_id = dataset.team_id
    dataset.team_id = team_id

    await log_audit(
        db,
        user_id=current_user.id,
        event_type="dataset_shared_to_team",
        team_id=team_id,
        resource_type="dataset",
        resource_id=dataset_id,
        detail={
            "dataset_id": dataset_id,
            "dataset_name": dataset.name,
            "team_id": team_id,
            "old_team_id": old_team_id,
        },
    )
    await db.commit()
    return {"dataset_id": dataset_id, "team_id": team_id}


@router.delete("/datasets/{dataset_id}/share")
async def unshare_dataset(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消数据集共享 (仅数据集 owner)."""
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限取消共享")

    old_team_id = dataset.team_id
    dataset.team_id = None

    if old_team_id:
        await log_audit(
            db,
            user_id=current_user.id,
            event_type="dataset_unshared_from_team",
            team_id=old_team_id,
            resource_type="dataset",
            resource_id=dataset_id,
            detail={
                "dataset_id": dataset_id,
                "dataset_name": dataset.name,
                "team_id": old_team_id,
            },
        )
    await db.commit()
    return {"success": True, "dataset_id": dataset_id, "team_id": None}
