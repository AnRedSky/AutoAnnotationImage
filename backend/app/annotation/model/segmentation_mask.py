"""
SegmentationMask ORM Model — app/annotation/model/
================================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/segmentation_mask.py 迁入 annotation 应用
"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.common.base_model import Base


class SegmentationMask(Base):
    __tablename__ = "segmentation_mask"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("image.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )
    mask_path: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(
        Enum("ai", "human", "human_corrected", name="mask_source"),
        default="human", nullable=False
    )
    annotated_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships (跨应用: 引用 tasks 下的 Image, admin 下的 User)
    image = relationship("Image", back_populates="segmentation_mask")

    def is_ai_generated(self) -> bool:
        return self.source == "ai"

    def __repr__(self) -> str:
        return f"<SegmentationMask {self.id} image={self.image_id} src={self.source}>"
