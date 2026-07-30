"""
DatasetMembership ORM Model — per-dataset 共享 (v3.2.0 MT-5)
============================================================

dataset owner (或 tenant_admin) 可把 dataset 分享给特定用户,
被分享的用户有 annotator 或 viewer 角色 (per-dataset).
"""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base_model import Base


DATASET_SHARE_ROLES = ("annotator", "viewer")


class DatasetMembership(Base):
    """Dataset 共享关系 (per-dataset 角色)."""
    __tablename__ = "dataset_membership"
    __table_args__ = (
        UniqueConstraint("dataset_id", "user_id", name="uq_dataset_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(
        Enum(*DATASET_SHARE_ROLES, name="dataset_share_role"),
        default="viewer", nullable=False,
    )
    assigned_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    dataset = relationship("Dataset", foreign_keys=[dataset_id])
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<DatasetMembership ds={self.dataset_id} user={self.user_id} role={self.role}>"
