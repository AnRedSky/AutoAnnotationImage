"""
Annotation Log ORM Model (审计用)
"""
from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.database import Base


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
        Enum("ai_predict", "confirm", "correct", "reject", name="annotation_action"),
        nullable=False
    )
    from_label_id: Mapped[int] = mapped_column(Integer, nullable=True)
    to_label_id: Mapped[int] = mapped_column(Integer, nullable=True)
    time_spent_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="annotations")
