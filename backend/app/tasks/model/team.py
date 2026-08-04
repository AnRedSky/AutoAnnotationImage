"""
Team ORM Model — 团队实体 (v3.3.0, v3.3.1 增强)
================================================

用户可创建团队, 邀请其他用户加入, 共享数据集进行协同标注.

角色:
  - leader (队长=创建者): 可管理成员 + 编辑数据集
  - annotator: 可标注
  - viewer: 只读

v3.3.1 增强:
  - tenant_id     (v3.2 多租户兼容预留, 当前未使用)
  - archived_at   (软删除标记, Phase L3 使用)
"""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base_model import Base


class Team(Base):
    """团队."""
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True,
    )
    max_members: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # v3.3.1: 多租户兼容 (v3.2 引入, 当前不强制使用)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True, default=None, index=True,
    )
    # v3.3.1: 软删除标记 (Phase L3 启用, 当前接口仍硬删)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=True, default=None, index=True,
    )

    # Relationships
    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    owner = relationship("User", foreign_keys=[owner_id])

    def is_archived(self) -> bool:
        """是否已归档 (软删除)."""
        return self.archived_at is not None

    def __repr__(self) -> str:
        return f"<Team {self.id} {self.name!r} owner={self.owner_id}>"
