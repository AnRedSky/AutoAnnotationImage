"""
Tenant ORM Model — 多租户组织实体 (v3.2.0 MT-1)
================================================

多租户重构的第一步: 创建 tenant 表, 所有业务数据按 tenant 隔离.

迁移路径 (docs/48 §2.4):
  - MT-1: 本文件 + User 加 tenant_id (nullable, default=1)
  - MT-2: user_tenant_role 关联表
  - MT-3: 业务表加 tenant_id

设计:
  - slug: URL 友好短名 (用于 JWT / 前端路径)
  - status: active | suspended (停用后 tenant 内用户不能登录)
  - max_users / max_datasets: 配额限制 (中间件层做配额检查)
"""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base


TENANT_STATUS_ACTIVE = "active"
TENANT_STATUS_SUSPENDED = "suspended"
TENANT_STATUS_VALUES = (TENANT_STATUS_ACTIVE, TENANT_STATUS_SUSPENDED)


class Tenant(Base):
    """租户 (组织/团队) 实体."""
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        Enum(*TENANT_STATUS_VALUES, name="tenant_status"),
        default=TENANT_STATUS_ACTIVE, nullable=False, index=True,
    )
    max_users: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    max_datasets: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<Tenant {self.id} {self.name!r} ({self.status})>"

    def is_active(self) -> bool:
        return self.status == TENANT_STATUS_ACTIVE
