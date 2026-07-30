"""
UserTenantRole ORM Model — per-tenant 用户角色 (v3.2.0 MT-2)
=============================================================

一个用户可属于多个 tenant, 每个 tenant 内有不同角色.

角色层级 (docs/48 §3.1):
  - tenant_admin: 管理 tenant 用户 + 看全部 dataset
  - annotator:   标注 assigned dataset
  - viewer:      只读

User.role 字段保留为 system_role (super_admin | user):
  - super_admin: 全局管理员 (不限 tenant)
  - user: 普通用户 (角色由 user_tenant_role 决定)
"""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base_model import Base


TENANT_ROLES = ("tenant_admin", "annotator", "viewer")


class UserTenantRole(Base):
    """用户↔租户↔角色 三元关系 (per-tenant 角色)."""
    __tablename__ = "user_tenant_role"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(
        Enum(*TENANT_ROLES, name="tenant_role"),
        default="annotator", nullable=False,
    )
    assigned_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], backref="tenant_roles")
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    def __repr__(self) -> str:
        return f"<UserTenantRole user={self.user_id} tenant={self.tenant_id} role={self.role}>"
