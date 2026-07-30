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
from app.middleware.tenant import TenantContext


async def log_audit(
    db: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[int] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """写入审计日志 (best-effort, 不抛异常阻断主流程).

    Args:
        db: 当前请求的 AsyncSession
        user_id: 操作者 ID
        event_type: 事件类型 (见 AUDIT_EVENT_TYPES)
        resource_type: 资源类型 (dataset / model / training_job / user)
        resource_id: 资源 ID
        detail: 额外细节 (JSON)
        ip_address: 客户端 IP
    """
    try:
        log = AuditLog(
            tenant_id=TenantContext.get(),
            user_id=user_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip_address=ip_address,
        )
        db.add(log)
        await db.flush()  # flush 但不 commit (由调用方 commit)
    except Exception:
        pass  # best-effort: 审计失败不阻断业务
