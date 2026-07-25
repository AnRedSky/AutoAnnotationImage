"""
Image ORM Model (Active Record) — app/tasks/model/
================================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/image.py 迁入 tasks 应用
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.common.base_model import Base


# 图片状态枚举
IMAGE_STATUS_PENDING = "pending"
IMAGE_STATUS_AI_LABELED = "ai_labeled"
IMAGE_STATUS_HUMAN_CONFIRMED = "human_confirmed"
IMAGE_STATUS_HUMAN_CORRECTED = "human_corrected"
IMAGE_STATUS_TRAINED = "trained"
IMAGE_STATUS_VALUES = (
    IMAGE_STATUS_PENDING, IMAGE_STATUS_AI_LABELED,
    IMAGE_STATUS_HUMAN_CONFIRMED, IMAGE_STATUS_HUMAN_CORRECTED, IMAGE_STATUS_TRAINED,
)
# 已确认状态 (用于导出过滤)
IMAGE_STATUS_CONFIRMED = (
    IMAGE_STATUS_HUMAN_CONFIRMED, IMAGE_STATUS_HUMAN_CORRECTED, IMAGE_STATUS_TRAINED,
)


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
        Enum(*IMAGE_STATUS_VALUES, name="image_status"),
        default=IMAGE_STATUS_PENDING, nullable=False, index=True
    )
    ai_prediction: Mapped[dict] = mapped_column(JSON, nullable=True)
    final_label_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("category.id"), nullable=True
    )
    task_type: Mapped[str] = mapped_column(
        Enum("classification", "detection", "segmentation", name="image_task_type"),
        default="classification", nullable=False, index=True
    )
    annotated_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )
    annotated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships (跨应用: 引用 tasks/annotation 下的模型)
    dataset = relationship("Dataset", back_populates="images")
    final_label = relationship("Category", foreign_keys=[final_label_id])
    bbox_annotations = relationship(
        "BBoxAnnotation", back_populates="image",
        cascade="all, delete-orphan", passive_deletes=True
    )
    segmentation_mask = relationship(
        "SegmentationMask", back_populates="image",
        cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )

    # ============== Active Record 业务方法 ==============

    def is_pending(self) -> bool:
        """是否待标注"""
        return self.status == IMAGE_STATUS_PENDING

    def is_confirmed(self) -> bool:
        """是否已确认 (human_confirmed / human_corrected / trained)"""
        return self.status in IMAGE_STATUS_CONFIRMED

    def has_ai_prediction(self) -> bool:
        """是否有 AI 预测结果"""
        return self.ai_prediction is not None

    def get_ai_top1_label(self) -> Optional[str]:
        """从 ai_prediction JSON 取 top1 类别名 (分类任务)"""
        if not self.ai_prediction:
            return None
        return self.ai_prediction.get("top1")

    def get_ai_top1_confidence(self) -> Optional[float]:
        """从 ai_prediction JSON 取 top1 置信度"""
        if not self.ai_prediction:
            return None
        return self.ai_prediction.get("top1_conf")

    def mark_ai_labeled(self, prediction: dict) -> None:
        """标记为 AI 已标注 (写入 ai_prediction, status=ai_labeled)"""
        self.ai_prediction = prediction
        self.status = IMAGE_STATUS_AI_LABELED

    def mark_confirmed(self, user_id: int, label_id: Optional[int] = None) -> None:
        """标记为人工确认 (status=human_confirmed)"""
        self.status = IMAGE_STATUS_HUMAN_CONFIRMED
        self.annotated_by = user_id
        self.annotated_at = datetime.utcnow()
        if label_id is not None:
            self.final_label_id = label_id

    def mark_corrected(self, user_id: int, label_id: Optional[int] = None) -> None:
        """标记为人工修正 (status=human_corrected)"""
        self.status = IMAGE_STATUS_HUMAN_CORRECTED
        self.annotated_by = user_id
        self.annotated_at = datetime.utcnow()
        if label_id is not None:
            self.final_label_id = label_id

    def __repr__(self) -> str:
        return f"<Image {self.id} {self.filename!r} status={self.status}>"
