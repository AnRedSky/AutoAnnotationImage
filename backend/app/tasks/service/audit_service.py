"""
Audit Log Service (v3.2.0 MT-8)
================================

权限敏感操作的审计日志写入 helper.
供 API 层在 P0/P1 端点里调用, 记录到 audit_log 表.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.audit_log import AuditLog


async def log_audit(
    db: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[int] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
    team_id: Optional[int] = None,
) -> None:
    """写入审计日志 (best-effort, 不抛异常阻断主流程)."""
    try:
        log = AuditLog(
            team_id=team_id,
            user_id=user_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip_address=ip_address,
        )
        db.add(log)
        await db.flush()
    except Exception:
        pass
