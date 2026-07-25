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
from app.common.enums import IMAGE_QUALITY_UNQUALIFIED


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

    # ============== 不合格标记 (v3.0.0 新增, 正交于 status 状态机) ==============
    # quality_flag: NULL 或 "unqualified"; 不影响 status, 撤销时清空即可
    quality_flag: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    # reject_reason: 预设枚举值 (blurry/wrong_category/duplicate/out_of_scope/violation/other)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # rejected_by: 标记人 (FK user.id); 撤销时清空
    rejected_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )
    # rejected_at: 标记时间; 撤销时清空
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

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

    # ============== 不合格标记 (正交于 status, 不修改原标注状态) ==============

    def mark_unqualified(self, user_id: int, reason: str) -> None:
        """标记为不合格图片 (设置 quality_flag, 不修改 status / final_label_id)

        - 保留原标注状态, 撤销后可完整恢复
        - reason: 预设枚举值 (见 RejectReason)
        """
        self.quality_flag = IMAGE_QUALITY_UNQUALIFIED
        self.reject_reason = reason
        self.rejected_by = user_id
        self.rejected_at = datetime.utcnow()

    def unmark_unqualified(self) -> None:
        """撤销不合格标记 (清空 4 字段, 恢复为正常图片)"""
        self.quality_flag = None
        self.reject_reason = None
        self.rejected_by = None
        self.rejected_at = None

    def is_unqualified(self) -> bool:
        """是否被标记为不合格"""
        return self.quality_flag == IMAGE_QUALITY_UNQUALIFIED

    def __repr__(self) -> str:
        return f"<Image {self.id} {self.filename!r} status={self.status}>"
