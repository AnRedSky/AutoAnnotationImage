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
        Enum("super_admin", "admin", "annotator", "viewer", name="user_role"),
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

    def is_admin(self) -> bool:
        """是否管理员 (全局 admin 或 super_admin)"""
        return self.role in ("admin", "super_admin")

    def is_super_admin(self) -> bool:
        """是否超级管理员 (跨 tenant)"""
        return self.role == "super_admin"

    def can_access_dataset(self, dataset) -> bool:
        """权限检查 (v3.2.0 MT 权限管理升级):

        1. super_admin → 全通
        2. admin (tenant_admin) → 同 tenant 全通
        3. owner → 自己创建的 dataset
        4. dataset_membership → 被共享的 dataset

        注: tenant_id 比较在中间件层自动过滤, 这里只做显式权限检查.
        """
        if self.is_super_admin():
            return True
        if self.is_admin():
            # admin 看同 tenant 的全部 (tenant_id 比较由中间件层过滤)
            return True
        # 普通用户: 自己创建的 dataset
        if dataset.owner_id == self.id:
            return True
        # 被共享的 dataset: 检查 dataset_membership
        # 注: 这里不查 DB (避免 N+1), 由调用方在做 API 层检查时查
        # 如果 dataset 有 _membership_cached 属性, 用它
        membership = getattr(dataset, "_membership_cached", None)
        if membership is not None:
            return self.id in membership
        return False

    def deactivate(self) -> None:
        """停用账号 (业务规则)"""
        self.is_active = False

    def activate(self) -> None:
        """激活账号"""
        self.is_active = True

    def __repr__(self) -> str:
        return f"<User {self.id} {self.username} ({self.role})>"
