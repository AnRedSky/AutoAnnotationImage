"""
TeamMember ORM Model — 团队成员 (v3.3.0)
=========================================

per-team 角色:
  - manager (可管理): 管理成员 + 配置数据集权限 + 编辑标注
  - editor (可编辑): 可对共享数据集进行标注
  - viewer (仅阅读): 只读
"""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base_model import Base


TEAM_ROLES = ("manager", "editor", "viewer")

# 可写角色: manager + editor (viewer 不可写)
WRITE_ROLES = ("manager", "editor")


class TeamMember(Base):
    """团队成员 (per-team 角色)."""
    __tablename__ = "team_member"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("team.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(
        Enum(*TEAM_ROLES, name="team_role"),
        default="editor", nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    team = relationship("Team", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])

    def can_manage(self) -> bool:
        """是否可管理 (manager)"""
        return self.role == "manager"

    def can_edit(self) -> bool:
        """是否可编辑 (manager + editor)"""
        return self.role in WRITE_ROLES

    def __repr__(self) -> str:
        return f"<TeamMember team={self.team_id} user={self.user_id} role={self.role}>"
