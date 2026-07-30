"""
AuditLog ORM Model — 通用审计日志 (v3.2.0 MT-6)
===================================================

所有权限敏感操作记录到 audit_log.
扩展现有 annotation_log (仅标注审计) 到通用审计:
  - 用户管理 (创建/停用/改角色)
  - dataset 管理 (创建/删除/共享)
  - 训练 (启训练/取消)
  - 模型 (激活/删除)
"""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base


AUDIT_EVENT_TYPES = (
    "user_login", "user_logout", "user_created", "user_deactivated",
    "role_changed", "dataset_created", "dataset_deleted",
    "dataset_shared", "dataset_unshared",
    "annotation_saved", "training_started", "training_cancelled",
    "model_activated", "model_deleted",
)


class AuditLog(Base):
    """通用审计日志 (跨模块, 按 tenant 归档)."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # v3.3.0: team_id (nullable, 审计可按团队归档)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("team.id"), nullable=True, default=None, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[int] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.id} {self.event_type} user={self.user_id}>"
