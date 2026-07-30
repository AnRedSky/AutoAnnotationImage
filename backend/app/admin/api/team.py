"""
Team Management API (v3.3.0)
==============================

团队管理端点:
  - GET    /api/teams              列出我的团队
  - POST   /api/teams              创建团队 (任何用户)
  - GET    /api/teams/{id}         团队详情
  - DELETE /api/teams/{id}         删除团队 (仅队长)
  - GET    /api/teams/{id}/members  团队成员列表
  - POST   /api/teams/{id}/members  邀请成员 (仅队长)
  - DELETE /api/teams/{id}/members/{uid}  移除成员 (仅队长)
  - POST   /api/datasets/{id}/share  把数据集共享给团队 (仅 owner)
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember, TEAM_ROLES
from app.tasks.model.dataset import Dataset
from app.middleware.http.auth import get_current_user

router = APIRouter()


# ============== Schemas ==============

class TeamCreate(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    max_members: int = 20


class MemberInvite(BaseModel):
    user_id: int
    role: str = "annotator"


# ============== Team CRUD ==============

@router.get("")
async def list_my_teams(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出我加入的团队."""
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
    """创建团队 (任何已登录用户)."""
    # slug 唯一检查
    existing = await db.execute(select(Team).where(Team.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Team slug '{body.slug}' already exists")

    team = Team(
        name=body.name,
        slug=body.slug,
        description=body.description,
        owner_id=current_user.id,
        max_members=body.max_members,
    )
    db.add(team)
    await db.flush()

    # 创建者自动加入为 leader
    member = TeamMember(
        team_id=team.id,
        user_id=current_user.id,
        role="leader",
    )
    db.add(member)
    await db.commit()
    await db.refresh(team)
    return {"id": team.id, "name": team.name, "slug": team.slug}


@router.get("/{team_id}")
async def get_team(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """团队详情 (仅成员可见)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")

    # 权限: admin 或团队成员
    if not current_user.is_admin():
        tm = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == current_user.id,
            )
        )
        if not tm.scalar_one_or_none():
            raise HTTPException(403, "无权限查看此团队")

    return {
        "id": team.id,
        "name": team.name,
        "slug": team.slug,
        "description": team.description,
        "owner_id": team.owner_id,
        "max_members": team.max_members,
        "created_at": team.created_at.isoformat() if team.created_at else None,
    }


@router.delete("/{team_id}")
async def delete_team(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除团队 (仅队长或 admin)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if not current_user.is_admin() and team.owner_id != current_user.id:
        raise HTTPException(403, "仅队长可删除团队")

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
    """团队成员列表 (仅成员可见)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if not current_user.is_admin():
        tm = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == current_user.id,
            )
        )
        if not tm.scalar_one_or_none():
            raise HTTPException(403, "无权限查看此团队")

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
    """邀请成员加入团队 (仅队长或 admin)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if not current_user.is_admin() and team.owner_id != current_user.id:
        raise HTTPException(403, "仅队长可邀请成员")

    if body.role not in TEAM_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}")

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
        raise HTTPException(409, "User already in this team")

    # 配额检查
    count_result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id)
    )
    if len(count_result.scalars().all()) >= team.max_members:
        raise HTTPException(400, f"Team is full (max {team.max_members})")

    member = TeamMember(
        team_id=team_id,
        user_id=body.user_id,
        role=body.role,
    )
    db.add(member)
    await db.commit()
    return {"team_id": team_id, "user_id": body.user_id, "role": body.role}


@router.delete("/{team_id}/members/{user_id}")
async def remove_member(
    team_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """移除成员 (仅队长或 admin; 队长不能被移除)."""
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if not current_user.is_admin() and team.owner_id != current_user.id:
        raise HTTPException(403, "仅队长可移除成员")
    if user_id == team.owner_id:
        raise HTTPException(400, "Cannot remove team owner")

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Member not found")

    await db.delete(member)
    await db.commit()
    return {"success": True, "detail": f"User {user_id} removed from team {team_id}"}


# ============== 数据集共享 ==============

@router.post("/datasets/{dataset_id}/share")
async def share_dataset_to_team(
    dataset_id: int,
    team_id: int = Query(..., description="目标团队 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """把数据集共享给团队 (仅 owner 或 admin)."""
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if not current_user.is_admin() and dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限共享此数据集")

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")

    dataset.team_id = team_id
    await db.commit()
    return {"dataset_id": dataset_id, "team_id": team_id}


@router.delete("/datasets/{dataset_id}/share")
async def unshare_dataset(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消数据集共享 (仅 owner 或 admin)."""
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if not current_user.is_admin() and dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限取消共享")

    dataset.team_id = None
    await db.commit()
    return {"success": True, "dataset_id": dataset_id, "team_id": None}
