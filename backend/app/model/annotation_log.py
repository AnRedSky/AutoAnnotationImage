"""
AnnotationLog ORM Model (审计)
=============================
"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.model.base import Base


class AnnotationLog(Base):
    __tablename__ = "annotation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("image.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(
        Enum(
            "ai_predict", "confirm", "correct", "reject",
            "auto_annotate_pretrained", "auto_annotate_finetuned",
            name="annotation_action",
        ),
        nullable=False
    )
    from_label_id: Mapped[int] = mapped_column(Integer, nullable=True)
    to_label_id: Mapped[int] = mapped_column(Integer, nullable=True)
    time_spent_ms: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="annotations")

    def __repr__(self) -> str:
        return f"<AnnotationLog {self.id} {self.action} image={self.image_id} user={self.user_id}>"
