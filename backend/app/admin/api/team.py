"""
Team Management API (v3.3.0 + v3.3.1 增强)
============================================

团队管理端点 (功能入口公开 + 数据严格隔离):
  - GET    /api/teams                       列出我创建和加入的团队 (数据隔离)
  - POST   /api/teams                       创建团队 (任何已登录用户)
  - GET    /api/teams/{id}                  团队详情 (仅成员可见)
  - PATCH  /api/teams/{id}                  编辑团队 (v3.3.1, manager/owner)
  - DELETE /api/teams/{id}                  软删除团队 (v3.3.1 L3, 仅创建者)
  - POST   /api/teams/{id}/restore          恢复已归档团队 (v3.3.1 L3, 仅 admin)
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
  - L3 软删除: archived_at + 列表过滤 + 管理员恢复

软删除行为 (v3.3.1 L3):
  - DELETE 设置 archived_at = utcnow(), 记录审计
  - 列表/详情自动过滤 (include_archived 仅 admin 可用)
  - 数据集 team_id 字段被清空 (避免悬挂引用)
  - 团队成员保留, 但无法访问
  - admin 可调用 POST /restore 恢复
"""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember, TEAM_ROLES, WRITE_ROLES
from app.tasks.model.dataset import Dataset
from app.tasks.model.audit_log import AuditLog
from app.middleware.http.auth import get_current_user
from app.tasks.service.audit_service import log_audit
from app.common.cache import cache

router = APIRouter()


# ============== 缓存键管理 (v3.3.1 L3) ==============

# 团队详情缓存 TTL: 5 分钟
TEAM_DETAIL_TTL = 300


def _team_detail_key(team_id: int) -> str:
    """团队详情缓存 key."""
    return f"team:detail:{team_id}"


def _invalidate_team_detail(team_id: int) -> None:
    """使团队详情缓存失效 (写操作统一调用)."""
    cache.delete(_team_detail_key(team_id))


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


async def _assert_can_manage(db: AsyncSession, team: Team, user: User) -> None:
    """要求可管理权限 (manager) 或 owner.

    v3.3.4 (P1-5 加固): 不再返回伪造的 TeamMember 对象
      - 旧实现: owner 若不在 team_member 表, 会构造一个游离的 TeamMember
        实例返回, 潜在风险: 该游离对象如果被误用 (如 db.add 或属性修改),
        可能产生悬挂引用或脏数据
      - 新实现: 返回 None 表示 owner (由调用方按 owner 语义处理),
        普通成员校验失败时直接 raise

    注意: 不再对 admin 绕过 — 非团队成员(包括管理员)不可操作.
    """
    if team.owner_id == user.id:
        # owner 自动有 manager 权限 — 不返回 TeamMember 对象
        return
    member = await _get_member_or_403(db, team.id, user.id)
    if not member.can_manage():
        raise HTTPException(403, "无权限: 需要「可管理」角色")


async def _count_managers(db: AsyncSession, team_id: int) -> int:
    """统计团队中 manager 数量 (用于 L2 阶段: 最后一名 manager 保护)."""
    result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team_id,
            TeamMember.role == "manager",
        )
    )
    return result.scalar() or 0


def _assert_team_active(team: Team) -> None:
    """v3.3.1 L3: 校验团队未归档 (已归档则 410 Gone).

    - 已归档的团队不可被成员访问 (数据隔离 + 业务停止)
    - 列表端点直接过滤, 此校验用于详情/写操作
    - admin 可绕过 (用于恢复操作)
    """
    if team.archived_at is not None:
        raise HTTPException(410, "团队已归档, 无法操作")


# ============== Team CRUD ==============

@router.get("")
async def list_my_teams(
    page: int = Query(default=1, ge=1, description="页码 (从 1 开始)"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数 (1-100)"),
    search: Optional[str] = Query(default=None, description="按团队名/描述模糊搜索"),
    sort: str = Query(
        default="id_desc",
        regex="^(id_desc|id_asc|name_asc|name_desc|member_count_desc|created_desc|created_asc)$",
        description="排序方式",
    ),
    include_archived: bool = Query(default=False, description="是否包含已归档团队 (仅 super_admin)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出我创建和加入的团队 (数据隔离: 仅可见自己所属的团队).

    v3.3.1 L3 增强:
      - 默认过滤 archived_at IS NULL (隐藏已归档)
      - include_archived=true 仅 super_admin 可用, 显示全部
      - 分页: page / page_size (默认 20, 最大 100)
      - 搜索: search 参数模糊匹配 name / description / slug (大小写不敏感)
      - 排序: sort 参数支持 7 种 (id 默认 desc, name, member_count, created_at)

    v3.3.6-STATS-ISOLATION 收紧:
      - include_archived 参数仅 super_admin 可生效
      - regular admin 传入 true 时被忽略, 走默认过滤 (与普通用户一致)
      - 原因: 归档团队仅 super_admin 运维场景需要查看
    """
    from sqlalchemy import or_
    from app.tasks.model.team_member import TeamMember as _TM

    # 0. 子查询: 团队成员数 (独立统计, 不受外层 WHERE 影响)
    # 注意: 不能直接在主查询里 COUNT(team_member.user_id), 因为外层已经
    # 通过 WHERE 限定到当前用户, 会导致 count 恒为 1. 必须用子查询.
    member_count_subq = (
        select(_TM.team_id, func.count(_TM.user_id).label("mc"))
        .group_by(_TM.team_id)
        .subquery()
    )

    # 1. 基础查询
    base = (
        select(Team, _TM.role, member_count_subq.c.mc.label("member_count"))
        .join(_TM, _TM.team_id == Team.id)
        .join(member_count_subq, member_count_subq.c.team_id == Team.id)
        .where(_TM.user_id == current_user.id)
    )
    if not include_archived or not current_user.is_super_admin():
        base = base.where(Team.archived_at.is_(None))

    # 2. 搜索 (name / description / slug 模糊)
    if search:
        pattern = f"%{search.strip()}%"
        base = base.where(
            or_(
                Team.name.ilike(pattern),
                Team.description.ilike(pattern),
                Team.slug.ilike(pattern),
            )
        )

    # 3. 排序
    if sort == "name_asc":
        base = base.order_by(Team.name.asc(), Team.id.asc())
    elif sort == "name_desc":
        base = base.order_by(Team.name.desc(), Team.id.asc())
    elif sort == "created_asc":
        base = base.order_by(Team.created_at.asc(), Team.id.asc())
    elif sort == "created_desc":
        base = base.order_by(Team.created_at.desc(), Team.id.asc())
    elif sort == "member_count_desc":
        base = base.order_by(member_count_subq.c.mc.desc(), Team.id.asc())
    elif sort == "id_asc":
        base = base.order_by(Team.id.asc())
    else:  # id_desc (默认)
        base = base.order_by(Team.id.desc())

    # 4. 计数
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 5. 分页
    offset = (page - 1) * page_size
    page_stmt = base.offset(offset).limit(page_size)
    result = await db.execute(page_stmt)
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
                "member_count": member_count,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "archived_at": t.archived_at.isoformat() if t.archived_at else None,
            }
            for t, role, member_count in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
        "sort": sort,
        "search": search,
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
    """团队详情 (仅成员可见, 数据隔离).

    v3.3.1 L3:
      - 已归档团队仅 admin 可见 (用于恢复), 其他用户 410.
      - Redis 缓存: 5 分钟 TTL, 写操作后失效.
    """
    # 1. 权限校验 (缓存前必须先校验, 避免权限绕过)
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    # 数据隔离: 非成员不可访问 (包括管理员)
    await _get_member_or_403(db, team_id, current_user.id)
    # 已归档团队对非 admin 返回 410
    if not current_user.is_admin():
        _assert_team_active(team)

    # 2. 缓存命中检查
    cache_key = _team_detail_key(team_id)
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    # 3. 缓存未命中, 实时构建响应
    data = {
        "id": team.id,
        "name": team.name,
        "slug": team.slug,
        "description": team.description,
        "owner_id": team.owner_id,
        "max_members": team.max_members,
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "archived_at": team.archived_at.isoformat() if team.archived_at else None,
        "_cached": True,  # 标记已缓存 (debug 用)
    }

    # 4. 写入缓存
    cache.set(cache_key, data, ttl=TEAM_DETAIL_TTL)
    return data


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
      5. v3.3.1 L3: 已归档团队不可编辑
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    await _assert_can_manage(db, team, current_user)
    _assert_team_active(team)  # v3.3.1 L3

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
        # v3.3.1 L3: 失效团队详情缓存
        _invalidate_team_detail(team_id)

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

    v3.3.1 L3 软删除改造:
      - 不再级联硬删 (members cascade 仍然存在, 但实际触发的是设置 archived_at)
      - 实际行为: 设置 archived_at = utcnow(), 记录审计
      - 列表/详情自动过滤 archived_at IS NULL (非 admin 不可见)
      - 数据集 team_id 字段被清空 (避免悬挂引用)
      - admin 可通过 POST /api/teams/{id}/restore 恢复
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if team.owner_id != current_user.id:
        raise HTTPException(403, "仅创建者可删除团队")
    if team.archived_at is not None:
        raise HTTPException(400, "团队已归档, 无需重复删除")

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

    # 软删除: 设置 archived_at
    team.archived_at = datetime.utcnow()

    # 审计
    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_deleted",
        team_id=team_id,
        resource_type="team",
        resource_id=team_id,
        detail={"name": team.name, "member_count": member_count, "soft_delete": True},
    )

    await db.commit()
    # v3.3.1 L3: 失效团队详情缓存
    _invalidate_team_detail(team_id)
    return {
        "success": True,
        "detail": f"Team {team_id} 已归档 (软删除)",
        "archived_at": team.archived_at.isoformat(),
    }


@router.post("/{team_id}/restore")
async def restore_team(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """恢复已归档团队 (v3.3.1 L3).

    权限: 仅系统管理员 (admin) 可恢复
      - 软删除具有破坏性, 必须有平台级管控
      - 普通用户若想恢复, 需联系管理员

    限制:
      - 团队必须存在 (无论归档与否)
      - 必须处于 archived 状态 (否则 400)
      - 恢复后 archived_at 清空, 团队恢复正常访问
    """
    # 管理员权限校验
    if not current_user.is_admin():
        raise HTTPException(403, "无权限: 仅系统管理员可恢复归档团队")

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if team.archived_at is None:
        raise HTTPException(400, "团队未归档, 无需恢复")

    # 记录恢复前的归档时间 (审计)
    archived_at_before = team.archived_at.isoformat()

    # 清空 archived_at
    team.archived_at = None

    # 审计
    await log_audit(
        db,
        user_id=current_user.id,
        event_type="team_restored",
        team_id=team_id,
        resource_type="team",
        resource_id=team_id,
        detail={
            "name": team.name,
            "owner_id": team.owner_id,
            "archived_at_before": archived_at_before,
        },
    )

    await db.commit()
    await db.refresh(team)
    # v3.3.1 L3: 失效团队详情缓存 (恢复后状态变更)
    _invalidate_team_detail(team_id)
    return {
        "success": True,
        "detail": f"Team {team_id} 已恢复",
        "team": {
            "id": team.id,
            "name": team.name,
            "slug": team.slug,
            "owner_id": team.owner_id,
        },
    }


# ============== 成员管理 ==============

@router.get("/{team_id}/members")
async def list_members(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """团队成员列表 (仅成员可见, 数据隔离).

    v3.3.3 增强 (用户新需求 §3):
      - 返回每条 member 的 is_owner 字段
        (与 team.owner_id 相等时为 True)
      - 团队所有者 (创建者) 在前端显示「可管理（所有者）」标签
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    # 数据隔离: 非成员不可访问
    await _get_member_or_403(db, team_id, current_user.id)
    # v3.3.1 L3: 已归档团队 (非 admin) 不可查看
    if not current_user.is_admin():
        _assert_team_active(team)

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
                # v3.3.3: 是否为团队创建者 (所有者)
                # — 用于前端在「角色」列额外标注「（所有者）」
                "is_owner": r[1].id == team.owner_id,
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
    _assert_team_active(team)  # v3.3.1 L3

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
    _assert_team_active(team)  # v3.3.1 L3: 已归档团队不可退队

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
    """修改成员角色 (仅 manager/owner).

    v3.3.1 L2: 最后一名 manager 保护 — 防止团队无 manager.
    v3.3.1 L3: 已归档团队不可变更成员.
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    await _assert_can_manage(db, team, current_user)
    _assert_team_active(team)  # v3.3.1 L3

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

    # v3.3.1 L2: 最后一名 manager 保护
    if old_role == "manager" and body.role != "manager":
        if await _count_managers(db, team_id) <= 1:
            raise HTTPException(
                400,
                "不能降级最后一名「可管理」成员,"
                "请先提升其他成员为「可管理」或转让团队所有权",
            )

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
    """移除成员 (仅 manager/owner; owner 不能被移除).

    v3.3.1 L2: 最后一名 manager 保护 — 防止团队无 manager.
    v3.3.1 L3: 已归档团队不可移除成员.
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    await _assert_can_manage(db, team, current_user)
    _assert_team_active(team)  # v3.3.1 L3

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

    # v3.3.1 L2: 最后一名 manager 保护
    if member.role == "manager":
        if await _count_managers(db, team_id) <= 1:
            raise HTTPException(
                400,
                "不能移除最后一名「可管理」成员,"
                "请先提升其他成员为「可管理」或转让团队所有权",
            )

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
      7. v3.3.1 L3: 已归档团队不可转让
    """
    if not body.confirm:
        raise HTTPException(400, "请确认转让操作 (设置 confirm=true)")

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if team.owner_id != current_user.id:
        raise HTTPException(403, "仅创建者可转让所有权")
    _assert_team_active(team)  # v3.3.1 L3

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
    category_count, status, owner_id, owner_name, my_access, my_access_label.

    v3.3.1 L3: 已归档团队不可查看数据集列表.
    v3.3.2 增强: my_access 增加中文标签 my_access_label (用于前端展示).
    """
    from app.tasks.model.team_member import ROLE_LABELS

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    # 数据隔离: 非成员不可访问
    member = await _get_member_or_403(db, team_id, current_user.id)
    _assert_team_active(team)  # v3.3.1 L3

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
                # v3.3.2: 中文标签 (前端直接展示)
                "my_access_label": ROLE_LABELS.get(member.role, member.role),
            }
            for d, owner_name in rows
        ]
    }


# ============== v3.3.2: 团队级「可共享数据集」端点 ==============

@router.get("/{team_id}/shareable-datasets")
async def list_shareable_datasets(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """v3.3.2: 列出「可共享给本团队」的数据集 (用户新需求 §1).

    用途: 团队管理页「共享数据集」按钮, 弹出选择器.
    业务规则:
      1. 当前用户必须是本团队成员 (数据隔离)
      2. 当前用户在团队中必须是 manager 角色
         (与「共享权限控制」一致, 普通成员无共享权)
      3. 团队未归档
      4. 返回当前用户拥有且未共享给本团队的 dataset
         - 排除: 已共享给本团队 (避免重复共享)
         - 排除: 已共享给其他团队 (避免 1 个 dataset 同时挂 2 个 team_id,
           当前数据模型 Dataset.team_id 是单值, 不能复用)
      5. 排除状态为 processing 的 (避免在上传/AI 预标注中共享)

    返回字段: id, name, task_type, image_count, annotated_count,
    category_count, status, created_at.
    """
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    member = await _get_member_or_403(db, team_id, current_user.id)
    _assert_team_active(team)

    # 仅 manager 可发起共享 (与 share_dataset_to_team 一致)
    if member.role != "manager" and team.owner_id != current_user.id:
        raise HTTPException(403, "无权限: 需要「可管理」角色才能共享数据集")

    # 当前用户拥有的、未共享给本团队、未共享给其他团队 (team_id IS NULL)
    result = await db.execute(
        select(Dataset)
        .where(
            Dataset.owner_id == current_user.id,
            # 团队共享字段为空, 即尚未分享给任何团队
            Dataset.team_id.is_(None),
            # 排除上传/处理中状态
            Dataset.status.in_(("draft", "done")),
        )
        .order_by(Dataset.id.desc())
    )
    datasets = result.scalars().all()

    return {
        "items": [
            {
                "id": d.id,
                "name": d.name,
                "task_type": d.task_type,
                "image_count": d.image_count,
                "annotated_count": d.annotated_count,
                "category_count": d.category_count,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in datasets
        ],
        "total": len(datasets),
    }


# ============== 数据集共享 ==============

@router.post("/datasets/{dataset_id}/share")
async def share_dataset_to_team(
    dataset_id: int,
    team_id: int = Query(..., description="目标团队 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """把数据集共享给团队 (v3.3.2 权限升级).

    权限校验 (统一在 permission_service.assert_can_share_to_team 中):
      1. admin 绕过
      2. 仅数据集 owner 可发起共享
      3. 当前用户必须是目标团队成员
      4. 目标团队内必须是 manager
      5. 团队未归档
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")

    # v3.3.2: 升级为统一权限检查 (含 manager 校验)
    from app.tasks.service.permission_service import assert_can_share_to_team
    await assert_can_share_to_team(db, current_user, dataset, team_id)

    # 团队未归档 (admin 也会被该校验拦下, 避免误操作)
    _assert_team_active(team)

    # 二次保护: 若 dataset 已共享给别的团队, 先清空 (因为 team_id 是单值)
    if dataset.team_id and dataset.team_id != team_id:
        # 写入旧团队的「取消共享」审计
        old_team_id = dataset.team_id
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
                "auto_replaced": True,  # 标记自动解除 (被新共享覆盖)
            },
        )
        old_team_id_for_log = old_team_id
    else:
        old_team_id_for_log = None

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
            "old_team_id": old_team_id_for_log,
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
    """取消数据集共享 (v3.3.2 权限明确: 仅原始共享者).

    业务规则 (与用户新需求 §4「共享权限控制」一致):
      - 仅数据集的原始共享者 (owner) 可取消共享
      - 仅 super_admin 可绕过 (平台级运维场景, 用于 owner 失联时的代管)
      - regular admin 不再具备取消他人共享的权限 (越权风险)
      - 与团队角色无关 (即使 manager 也不能替 owner 取消)

    v3.3.6-STATS-ISOLATION 收紧:
      - 移除 is_admin() 旁路 → 收紧为 is_super_admin() 旁路
      - 原因: regular admin 通过取消共享可破坏别人的协作关系 (横向越权),
              仅保留 super_admin 作为平台代管
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # v3.3.6: 严格校验 — 仅 owner / super_admin 可取消
    # regular admin 不再具备该权限 (避免横向越权破坏协作)
    if (
        not current_user.is_super_admin()
        and dataset.owner_id != current_user.id
    ):
        raise HTTPException(403, "无权限取消共享: 仅数据集原始共享者或超级管理员可操作")

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


# ============== 团队活动 Feed (v3.3.1 L4 + v3.3.3 中文增强) ==============

# 活动事件类型语义化映射 (前端可直接显示)
_TEAM_ACTIVITY_LABELS = {
    "team_created": "创建团队",
    "team_updated": "更新团队",
    "team_deleted": "归档团队",
    "team_restored": "恢复团队",
    "team_ownership_transferred": "转让所有权",
    "team_left": "退出团队",
    "team_member_invited": "邀请成员",
    "team_member_role_changed": "变更成员角色",
    "team_member_removed": "移除成员",
    "dataset_shared_to_team": "共享数据集",
    "dataset_unshared_from_team": "取消共享数据集",
}


def _build_activity_message(
    log: AuditLog,
    actor_name: str,
    role_label_map: dict[int, str],
    user_name_map: dict[int, str],
    team_name_map: dict[int, str],
    dataset_name_map: dict[int, str],
) -> str:
    """v3.3.3: 生成团队动态的中文自然语言描述.

    业务规则 (与用户新需求 §4「团队动态记录规范」对齐):
      - 统一使用通俗易懂的规范中文表述
      - 避免直接使用代码变量名、技术术语、英文表述
      - 优先以「人 + 动作 + 资源」三段式表达
      - detail 中能解析的字段全部翻译为自然语言

    Args:
        log: 审计日志记录
        actor_name: 触发人用户名 (如 "alice")
        role_label_map: user_id -> 中文角色标签 (如 {1: "可管理", 2: "可编辑"})
        user_name_map: user_id -> 用户名 (用于转让/邀请/移除等场景)
        team_name_map: team_id -> 团队名 (备用, 实际 event 中通常一致)
        dataset_name_map: dataset_id -> 数据集名 (用于共享/取消共享场景)

    Returns:
        自然语言描述, 如:
          "alice 创建了团队「标注组A」"
          "bob 邀请了 carol 加入团队, 角色为「可编辑」"
          "alice 把数据集「猫狗分类」共享给了团队「标注组A」"
    """
    detail = log.detail or {}
    et = log.event_type
    res_type = log.resource_type
    res_id = log.resource_id

    if et == "team_created":
        name = detail.get("name", f"团队{log.team_id}")
        return f"{actor_name} 创建了团队「{name}」"

    if et == "team_updated":
        changes = detail.get("changes") or {}
        if not changes:
            return f"{actor_name} 更新了团队信息"
        # 字段 → 中文标签
        field_map = {
            "name": "名称",
            "description": "描述",
            "max_members": "成员上限",
            "slug": "标识",
        }
        parts = []
        for field, ch in changes.items():
            label = field_map.get(field, field)
            old = ch.get("old")
            new = ch.get("new")
            if field == "max_members":
                parts.append(f"{label}从 {old} 调整为 {new}")
            else:
                parts.append(f"{label}修改为「{new}」")
        return f"{actor_name} 更新了团队: " + "; ".join(parts)

    if et == "team_deleted":
        member_count = detail.get("member_count", 0)
        return f"{actor_name} 归档了团队 (含 {member_count} 名成员)"

    if et == "team_restored":
        return f"{actor_name} 恢复了已归档的团队"

    if et == "team_ownership_transferred":
        old_id = detail.get("old_owner")
        new_id = detail.get("new_owner")
        new_name = user_name_map.get(new_id, f"用户{new_id}") if new_id else ""
        return f"{actor_name} 将团队所有权转让给了「{new_name}」"

    if et == "team_left":
        old_role = detail.get("old_role", "")
        role_cn = role_label_map.get(-1, "")  # 兜底
        # 优先用 detail.old_role 解析
        try:
            from app.tasks.model.team_member import ROLE_LABELS as _RL
            role_cn = _RL.get(old_role, old_role or "")
        except Exception:
            pass
        return f"{actor_name} 退出了团队 (原角色: {role_cn or '成员'})"

    if et == "team_member_invited":
        invitee = detail.get("invitee_username", f"用户{detail.get('invitee_id', '')}")
        role = detail.get("role", "")
        try:
            from app.tasks.model.team_member import ROLE_LABELS as _RL
            role_cn = _RL.get(role, role or "成员")
        except Exception:
            role_cn = role or "成员"
        return f"{actor_name} 邀请了「{invitee}」加入团队, 角色为「{role_cn}」"

    if et == "team_member_role_changed":
        old_role = detail.get("old_role", "")
        new_role = detail.get("new_role", "")
        target_id = res_id or detail.get("user_id")
        target_name = user_name_map.get(target_id, f"用户{target_id}") if target_id else "成员"
        try:
            from app.tasks.model.team_member import ROLE_LABELS as _RL
            old_cn = _RL.get(old_role, old_role or "未知")
            new_cn = _RL.get(new_role, new_role or "未知")
        except Exception:
            old_cn = old_role or "未知"
            new_cn = new_role or "未知"
        return (
            f"{actor_name} 将「{target_name}」的角色从「{old_cn}」"
            f"调整为「{new_cn}」"
        )

    if et == "team_member_removed":
        removed_id = detail.get("removed_id") or res_id
        target_name = (
            user_name_map.get(removed_id, f"用户{removed_id}")
            if removed_id else "成员"
        )
        old_role = detail.get("old_role", "")
        try:
            from app.tasks.model.team_member import ROLE_LABELS as _RL
            role_cn = _RL.get(old_role, old_role or "成员")
        except Exception:
            role_cn = old_role or "成员"
        return f"{actor_name} 将「{target_name}」从团队中移除 (原角色: {role_cn})"

    if et == "dataset_shared_to_team":
        ds_id = res_id or detail.get("dataset_id")
        ds_name = (
            dataset_name_map.get(ds_id) or detail.get("dataset_name")
            or f"数据集{ds_id}"
        )
        team_id = log.team_id or detail.get("team_id")
        team_name = team_name_map.get(team_id) if team_id else None
        if team_name:
            return f"{actor_name} 把数据集「{ds_name}」共享给了团队「{team_name}」"
        return f"{actor_name} 把数据集「{ds_name}」共享到了团队"

    if et == "dataset_unshared_from_team":
        ds_id = res_id or detail.get("dataset_id")
        ds_name = (
            dataset_name_map.get(ds_id) or detail.get("dataset_name")
            or f"数据集{ds_id}"
        )
        team_id = log.team_id or detail.get("team_id")
        team_name = team_name_map.get(team_id) if team_id else None
        is_auto = detail.get("auto_replaced", False)
        suffix = " (由新共享自动覆盖)" if is_auto else ""
        if team_name:
            return f"{actor_name} 取消了在团队「{team_name}」的数据集「{ds_name}」共享{suffix}"
        return f"{actor_name} 取消了数据集「{ds_name}」的团队共享{suffix}"

    # 兜底: 未知事件类型
    label = _TEAM_ACTIVITY_LABELS.get(et, et)
    return f"{actor_name} {label}"


@router.get("/{team_id}/activities")
async def list_team_activities(
    team_id: int,
    limit: int = Query(default=50, ge=1, le=200, description="返回条数 (1-200)"),
    event_type: Optional[str] = Query(default=None, description="事件类型过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """v3.3.1 L4: 团队活动 Feed (团队成员可见).

    聚合最近 N 条团队相关 audit_log, JOIN User 拿 username + 事件类型语义化标签.
    用于团队详情页「动态」 Tab 展示.

    业务规则:
      - 仅团队成员可见 (数据隔离)
      - 已归档团队对非 admin 返回 410
      - 按 created_at DESC 排序 (最新在前)
      - event_type 可选过滤

    v3.3.3 增强 (用户新需求 §4):
      - 新增 detail_message 字段, 基于 event_type + detail 生成中文自然语言描述
      - 前端优先展示 detail_message, 避免直接显示英文 key / JSON

    响应字段:
      - items: [{id, user_id, username, event_type, event_label, detail_message,
                 resource_type, resource_id, detail, created_at}]
      - total: 符合条件的总条数
    """
    # 1. 权限与归档校验
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    # v3.3.4 (P1-4 加固): 移除 admin 旁路
    # 旧逻辑: admin 可绕过成员关系校验 (用于合规审计)
    # 新逻辑: 仅 super_admin 可访问未加入团队的活动 feed (审计场景),
    # regular admin 仍受团队成员关系约束, 避免横向越权
    if current_user.is_super_admin():
        # super_admin 可看任意团队活动 (审计)
        pass
    else:
        # 数据隔离: 非成员不可访问
        await _get_member_or_403(db, team_id, current_user.id)
        _assert_team_active(team)

    # 2. 基础查询
    conditions = [AuditLog.team_id == team_id]
    if event_type:
        conditions.append(AuditLog.event_type == event_type)

    # 3. 总数
    count_stmt = select(func.count(AuditLog.id)).where(and_(*conditions))
    total = (await db.execute(count_stmt)).scalar() or 0

    # 4. 分页查询 (按 created_at DESC, 最新在前)
    stmt = (
        select(AuditLog, User.username)
        .join(User, User.id == AuditLog.user_id)
        .where(and_(*conditions))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # ============== v3.3.3: 准备中文描述所需映射 ==============
    # 收集涉及的 user_id (含 detail.invitee_id / removed_id / new_owner / old_owner 等)
    user_ids: set[int] = set()
    dataset_ids: set[int] = set()
    for log, _username in rows:
        user_ids.add(log.user_id)
        detail = log.detail or {}
        for k in (
            "invitee_id", "removed_id", "old_owner", "new_owner",
            "user_id",
        ):
            v = detail.get(k)
            if isinstance(v, int):
                user_ids.add(v)
        # v3.3.3: team_member resource_id 是被操作成员的 user_id
        # (role_changed / removed 等事件), 需一并收集以构建中文描述
        if log.resource_type == "team_member" and log.resource_id:
            user_ids.add(log.resource_id)
        if log.resource_type == "dataset" and log.resource_id:
            dataset_ids.add(log.resource_id)
        if log.event_type in ("dataset_shared_to_team", "dataset_unshared_from_team"):
            v = detail.get("dataset_id")
            if isinstance(v, int):
                dataset_ids.add(v)

    # 批量查用户名 (触发人以外)
    user_name_map: dict[int, str] = {}
    if user_ids:
        names_q = await db.execute(
            select(User.id, User.username).where(User.id.in_(user_ids))
        )
        for uid, uname in names_q.all():
            user_name_map[uid] = uname

    # 批量查数据集名
    dataset_name_map: dict[int, str] = {}
    if dataset_ids:
        ds_q = await db.execute(
            select(Dataset.id, Dataset.name).where(Dataset.id.in_(dataset_ids))
        )
        for did, dname in ds_q.all():
            dataset_name_map[did] = dname

    # 团队名映射 (本端点通常都在同一 team 下, 简单构造)
    team_name_map: dict[int, str] = {team_id: team.name}

    # 角色标签映射 (供 detail 中的 old_role / new_role 翻译)
    role_label_map: dict[int, str] = {}

    return {
        "items": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "username": username,
                "event_type": log.event_type,
                "event_label": _TEAM_ACTIVITY_LABELS.get(log.event_type, log.event_type),
                # v3.3.3: 中文自然语言描述 (前端优先展示)
                "detail_message": _build_activity_message(
                    log,
                    actor_name=username,
                    role_label_map=role_label_map,
                    user_name_map=user_name_map,
                    team_name_map=team_name_map,
                    dataset_name_map=dataset_name_map,
                ),
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log, username in rows
        ],
        "total": total,
        "limit": limit,
        "event_type": event_type,
    }


# ============== 缓存监控 (v3.3.1 L4) ==============

@router.get("/_cache/stats")
async def get_cache_stats(
    current_user: User = Depends(get_current_user),
):
    """v3.3.1 L4: 缓存统计 (hit/miss 计数 + 命中率).

    权限: 仅 super_admin (运维监控)
    用途: 前端管理后台「缓存监控」卡片展示 hit_rate_percent + 趋势.

    v3.3.6-STATS-ISOLATION 收紧:
      - 旧逻辑: is_admin() (含 regular admin) 可查看
      - 新逻辑: 仅 super_admin 可查看
      - 原因: 缓存统计数据虽不直接泄露业务数据, 但含全局 hit/miss,
              regular admin 不应掌握全平台运行指标 (信息隔离)
    """
    if not current_user.is_super_admin():
        raise HTTPException(403, "无权限: 仅超级管理员可查看缓存统计")
    return cache.get_stats()
