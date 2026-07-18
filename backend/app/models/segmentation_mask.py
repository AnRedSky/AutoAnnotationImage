"""
Segmentation Mask ORM Model (v2.0.0 图像分割)

- 一张图对应一条 mask 记录 (1:1 关系, UNIQUE image_id)
- mask 物理存储: PNG 索引图 (P-mode), 像素值 = 类别索引
  - 像素值 0 = 背景 (未标注)
  - 像素值 N = Category.id = N 的类别
- mask_path 相对于 UPLOAD_DIR, 类似 Image.storage_path
- 调色板从 Category.color 实时渲染, 不存到 mask 文件
"""
from sqlalchemy import (
    String, Integer, DateTime, Enum, ForeignKey, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.database import Base


class SegmentationMask(Base):
    __tablename__ = "segmentation_mask"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 1 张图只对应 1 个 mask (UNIQUE 约束, 避免重复)
    image_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("image.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )
    # 相对 UPLOAD_DIR 的路径, 实际文件是 PNG 索引图
    mask_path: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    # 来源: ai / human / human_corrected
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

    # Relationships
    image = relationship("Image", back_populates="segmentation_mask")
