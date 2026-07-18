"""
Image ORM Model
"""
from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.database import Base


class Image(Base):
    __tablename__ = "image"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, nullable=True)
    height: Mapped[int] = mapped_column(Integer, nullable=True)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        Enum("pending", "ai_labeled", "human_confirmed", "human_corrected", name="image_status"),
        default="pending", nullable=False, index=True
    )
    ai_prediction: Mapped[dict] = mapped_column(JSON, nullable=True)
    final_label_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("category.id"), nullable=True
    )
    # v2.0.0: 任务类型冗余字段, 避免每次读 task_type 都要 join dataset
    # 默认为 classification 兼容 v1.0.0 历史数据
    task_type: Mapped[str] = mapped_column(
        Enum("classification", "detection", "segmentation", name="image_task_type"),
        default="classification", nullable=False, index=True
    )
    annotated_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )
    annotated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    dataset = relationship("Dataset", back_populates="images")
    final_label = relationship("Category", foreign_keys=[final_label_id])
    # v2.0.0: 检测 / 分割关系
    bbox_annotations = relationship(
        "BBoxAnnotation", back_populates="image",
        cascade="all, delete-orphan", passive_deletes=True
    )
    segmentation_mask = relationship(
        "SegmentationMask", back_populates="image",
        cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
