"""
BBox Annotation ORM Model (v2.0.0 目标检测)

- 一张图可以有多个 bbox (多目标场景)
- 坐标采用归一化 0-1 (与 YOLO txt 格式一致, 避免前端/后端坐标系转换)
- 物理像素坐标 = 归一化 × image.width / image.height
- source 区分 AI 推理 / 人工确认 / 人工修正, 配合 audit log 使用
"""
from sqlalchemy import (
    String, Integer, DateTime, Enum, ForeignKey, Float, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.database import Base


class BBoxAnnotation(Base):
    __tablename__ = "bbox_annotation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("image.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("category.id"), nullable=True, index=True
    )
    # 归一化坐标 (0-1), YOLO 格式
    x_min: Mapped[float] = mapped_column(Float, nullable=False)
    y_min: Mapped[float] = mapped_column(Float, nullable=False)
    x_max: Mapped[float] = mapped_column(Float, nullable=False)
    y_max: Mapped[float] = mapped_column(Float, nullable=False)
    # AI 推理时填, 人工标注为 None
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    # 来源: ai / human / human_corrected (与 AnnotationLog.action 对齐)
    source: Mapped[str] = mapped_column(
        Enum("ai", "human", "human_corrected", name="bbox_source"),
        default="human", nullable=False
    )
    annotated_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    image = relationship("Image", back_populates="bbox_annotations")
    category = relationship("Category")

    __table_args__ = (
        Index("idx_bbox_image_category", "image_id", "category_id"),
    )
