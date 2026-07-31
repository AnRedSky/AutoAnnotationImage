"""
User ORM Model (Active Record) — app/admin/model/
=================================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/user.py 迁入 admin 应用
**v3.0.0 Phase 1 新增**: Active Record 业务方法
**v3.3.0 重构**: 去掉 tenant_id, 改为 team 架构
"""
from sqlalchemy import String, Boolean, Integer, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.common.base_model import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(
        Enum("super_admin", "admin", "annotator", "viewer", name="user_role"),
        default="annotator", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships (跨应用: 引用 tasks 应用下的模型, 通过类名解析)
    datasets = relationship("Dataset", back_populates="owner", cascade="all, delete-orphan")
    annotations = relationship("AnnotationLog", back_populates="user")

    # ============== Active Record 业务方法 ==============

    def is_admin(self) -> bool:
        """是否管理员 (全局 admin 或 super_admin)"""
        return self.role in ("admin", "super_admin")

    def is_super_admin(self) -> bool:
        """是否超级管理员"""
        return self.role == "super_admin"

    def can_access_dataset(self, dataset) -> bool:
        """同步权限检查 (不查 DB):

        1. admin → 全通
        2. owner → 自己创建的
        3. 团队成员 → 需 API 层异步查 team_member (这里只做快速判断)

        注: 完整权限检查 (含 team) 由 API 层 can_access_dataset_async 做异步 DB 查询.
        """
        if self.is_admin():
            return True
        return dataset.owner_id == self.id

    def deactivate(self) -> None:
        """停用账号 (业务规则)"""
        self.is_active = False

    def activate(self) -> None:
        """激活账号"""
        self.is_active = True

    def __repr__(self) -> str:
        return f"<User {self.id} {self.username} ({self.role})>"
