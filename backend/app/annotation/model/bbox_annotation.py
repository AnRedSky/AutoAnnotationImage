"""
BBoxAnnotation ORM Model — app/annotation/model/
==============================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/bbox_annotation.py 迁入 annotation 应用
"""
from datetime import datetime
from typing import Tuple
from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, Float, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.common.base_model import Base


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
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
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

    # Relationships (跨应用: 引用 tasks 下的 Image/Category, admin 下的 User)
    image = relationship("Image", back_populates="bbox_annotations")
    category = relationship("Category")

    __table_args__ = (
        Index("idx_bbox_image_category", "image_id", "category_id"),
    )

    # ============== Active Record 业务方法 ==============

    def is_ai_generated(self) -> bool:
        return self.source == "ai"

    def is_human_corrected(self) -> bool:
        return self.source == "human_corrected"

    def width(self) -> float:
        return max(0.0, self.x_max - self.x_min)

    def height(self) -> float:
        return max(0.0, self.y_max - self.y_min)

    def area(self) -> float:
        """归一化面积 (0-1)"""
        return self.width() * self.height()

    def to_pixel_bbox(self, img_width: int, img_height: int) -> Tuple[float, float, float, float]:
        """转像素坐标 [x, y, w, h] (COCO 风格)"""
        if not img_width or not img_height:
            # 缺尺寸兜底: 用归一化值
            return (self.x_min, self.y_min, self.width(), self.height())
        x = self.x_min * img_width
        y = self.y_min * img_height
        w = (self.x_max - self.x_min) * img_width
        h = (self.y_max - self.y_min) * img_height
        return (x, y, w, h)

    def to_yolo_line(self, class_idx: int) -> str:
        """转 YOLO 格式单行: '<class_idx> <cx> <cy> <w> <h>'"""
        cx = (self.x_min + self.x_max) / 2
        cy = (self.y_min + self.y_max) / 2
        return f"{class_idx} {cx:.6f} {cy:.6f} {self.width():.6f} {self.height():.6f}\n"

    def __repr__(self) -> str:
        return (
            f"<BBox {self.id} image={self.image_id} cat={self.category_id} "
            f"src={self.source} conf={self.confidence}>"
        )
