"""
审计日志查询 API (v3.3.1 L4)
==============================

提供系统级审计日志的分页查询 + 过滤能力 (合规审计支持).

端点:
  - GET  /api/audit-logs        系统级审计查询 (仅 admin)

权限模型:
  - /api/audit-logs: 仅 admin (跨用户/跨团队, 涉及合规)

注: 团队级活动 Feed (/api/teams/{id}/activities) 见 team.py 端点,
    因为路径前缀冲突, 活动聚合属于团队域而非审计域.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.tasks.model.audit_log import AuditLog, AUDIT_EVENT_TYPES
from app.middleware.http.auth import get_current_user

router = APIRouter()


# v3.3.1 L4: 扩展审计事件类型白名单 (包含 L1 阶段新增的 11 个 team 相关事件)
_EXTENDED_AUDIT_EVENT_TYPES = AUDIT_EVENT_TYPES + (
    "team_created", "team_updated", "team_deleted", "team_restored",
    "team_ownership_transferred", "team_left",
    "team_member_invited", "team_member_role_changed", "team_member_removed",
    "dataset_shared_to_team", "dataset_unshared_from_team",
)


# ============== 系统级审计查询 (仅 admin) ==============

@router.get("/audit-logs")
async def list_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    event_type: Optional[str] = Query(default=None, description="事件类型过滤"),
    team_id: Optional[int] = Query(default=None, description="按团队过滤"),
    user_id: Optional[int] = Query(default=None, description="按用户过滤"),
    resource_type: Optional[str] = Query(default=None, description="按资源类型过滤"),
    start: Optional[datetime] = Query(default=None, description="起始时间 (ISO8601)"),
    end: Optional[datetime] = Query(default=None, description="截止时间 (ISO8601)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审计日志分页查询 (仅 super_admin).

    v3.3.1 L4: 支持多维度过滤 + 时间范围 + 排序 (created_at DESC).

    v3.3.6-STATS-ISOLATION 收紧 (严格最小权限):
      - 旧逻辑: is_admin() (含 regular admin) 可访问所有审计日志
      - 新逻辑: 仅 super_admin 可访问
      - 原因: 审计日志含 user_id + resource_id + ip_address 等敏感信息,
              regular admin 越权查看他人审计数据会泄露:
                1. 他人操作时间规律 (社会工程攻击)
                2. 系统级资源 ID 分布 (推算业务规模)
                3. 越权尝试的 IP + user_id (横向越权情报)
      - 业务场景: regular admin 仅需关注自己团队的审计 (走 /api/teams/{id}/activities)
    """
    if not current_user.is_super_admin():
        raise HTTPException(403, "无权限: 仅超级管理员可查看全平台审计日志")

    # 1. 基础条件
    conditions = []
    if event_type:
        if event_type not in _EXTENDED_AUDIT_EVENT_TYPES:
            raise HTTPException(400, f"无效事件类型: {event_type}, 可选: {_EXTENDED_AUDIT_EVENT_TYPES}")
        conditions.append(AuditLog.event_type == event_type)
    if team_id is not None:
        conditions.append(AuditLog.team_id == team_id)
    if user_id is not None:
        conditions.append(AuditLog.user_id == user_id)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if start:
        conditions.append(AuditLog.created_at >= start)
    if end:
        conditions.append(AuditLog.created_at <= end)

    # 2. 总数
    count_stmt = select(func.count(AuditLog.id))
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))
    total = (await db.execute(count_stmt)).scalar() or 0

    # 3. 分页查询 (按 created_at DESC, 最新在前)
    offset = (page - 1) * page_size
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    # 4. 收集 user_id 用于批量拉 username
    user_ids = {r.user_id for r in rows} - {current_user.id}
    username_map: dict = {current_user.id: current_user.username}
    if user_ids:
        users_result = await db.execute(
            select(User.id, User.username).where(User.id.in_(user_ids))
        )
        username_map.update({uid: uname for uid, uname in users_result.all()})

    return {
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": username_map.get(r.user_id, "unknown"),
                "event_type": r.event_type,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "team_id": r.team_id,
                "detail": r.detail,
                "ip_address": r.ip_address,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
        "filters": {
            "event_type": event_type,
            "team_id": team_id,
            "user_id": user_id,
            "resource_type": resource_type,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
    }

