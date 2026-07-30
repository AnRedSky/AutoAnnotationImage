"""
User ORM Model (Active Record) — app/admin/model/
=================================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/user.py 迁入 admin 应用
**v3.0.0 Phase 1 新增**: Active Record 业务方法
**v3.2.0 MT-1 新增**: tenant_id 字段 (多租户, nullable, default=1 兼容旧数据)
"""
from sqlalchemy import String, Boolean, Integer, DateTime, Enum, ForeignKey
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
        Enum("admin", "annotator", "viewer", name="user_role"),
        default="annotator", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # v3.2.0 MT-1: 多租户 — tenant_id 关联 tenant 表
    # nullable + 无 FK 约束: 兼容旧数据 (NULL → 视为 default tenant)
    # 后续 MT-4 中间件自动过滤时, NULL tenant_id 被视为 super_admin 级别
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenant.id"), nullable=True, default=1, index=True,
    )

    # Relationships (跨应用: 引用 tasks 应用下的模型, 通过类名解析)
    datasets = relationship("Dataset", back_populates="owner", cascade="all, delete-orphan")
    annotations = relationship("AnnotationLog", back_populates="user")

    # ============== Active Record 业务方法 ==============

    def is_admin(self) -> bool:
        """是否管理员"""
        return self.role == "admin"

    def is_annotator(self) -> bool:
        """是否标注员"""
        return self.role == "annotator"

    def can_access_dataset(self, dataset) -> bool:
        """权限检查: 管理员可访问全部, 否则只看自己的"""
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
