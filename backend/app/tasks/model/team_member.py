"""
TeamMember ORM Model — 团队成员 (v3.3.0)
=========================================

per-team 角色: leader / annotator / viewer
"""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base_model import Base


TEAM_ROLES = ("leader", "annotator", "viewer")


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
        default="annotator", nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    team = relationship("Team", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<TeamMember team={self.team_id} user={self.user_id} role={self.role}>"
