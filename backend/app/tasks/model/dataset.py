"""
Dataset ORM Model (Active Record) — app/tasks/model/
==================================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/dataset.py 迁入 tasks 应用
"""
from typing import Optional
from sqlalchemy import String, Integer, Text, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.common.base_model import Base


# 数据集状态枚举值
DATASET_STATUS_DRAFT = "draft"
DATASET_STATUS_ANNOTATING = "annotating"
DATASET_STATUS_TRAINING = "training"
DATASET_STATUS_DONE = "done"
DATASET_STATUS_VALUES = (DATASET_STATUS_DRAFT, DATASET_STATUS_ANNOTATING,
                         DATASET_STATUS_TRAINING, DATASET_STATUS_DONE)


class Dataset(Base):
    __tablename__ = "dataset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    task_type: Mapped[str] = mapped_column(
        Enum("classification", "detection", "segmentation", name="dataset_task_type"),
        default="classification", nullable=False
    )
    category_count: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    annotated_count: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*DATASET_STATUS_VALUES, name="dataset_status"),
        default=DATASET_STATUS_DRAFT, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships (跨应用: 引用 admin/tasks/annotation 下的模型, 通过类名解析)
    owner = relationship("User", back_populates="datasets")
    categories = relationship("Category", back_populates="dataset", cascade="all, delete-orphan")
    images = relationship("Image", back_populates="dataset", cascade="all, delete-orphan")

    # ============== Active Record 业务方法 ==============

    def is_classification(self) -> bool:
        return self.task_type == "classification"

    def is_detection(self) -> bool:
        return self.task_type == "detection"

    def is_segmentation(self) -> bool:
        return self.task_type == "segmentation"

    def is_draft(self) -> bool:
        return self.status == DATASET_STATUS_DRAFT

    def is_in_progress(self) -> bool:
        """是否处于进行中状态 (非 draft 非 done)"""
        return self.status in (DATASET_STATUS_ANNOTATING, DATASET_STATUS_TRAINING)

    def annotation_progress_pct(self) -> float:
        """标注进度百分比 (0-100)"""
        if self.image_count == 0:
            return 0.0
        return min(100.0, (self.annotated_count / self.image_count) * 100.0)

    def transition_to(self, new_status: str) -> None:
        """业务规则: 状态机转移 (非法转移抛异常)

        draft -> annotating -> training -> done
        """
        _transitions = {
            DATASET_STATUS_DRAFT: (DATASET_STATUS_ANNOTATING,),
            DATASET_STATUS_ANNOTATING: (DATASET_STATUS_TRAINING, DATASET_STATUS_DRAFT),
            DATASET_STATUS_TRAINING: (DATASET_STATUS_DONE, DATASET_STATUS_ANNOTATING),
            DATASET_STATUS_DONE: (),  # 终态
        }
        if new_status not in _transitions.get(self.status, ()):
            raise ValueError(
                f"非法数据集状态转移: {self.status!r} -> {new_status!r}"
            )
        self.status = new_status

    def __repr__(self) -> str:
        return f"<Dataset {self.id} {self.name!r} task={self.task_type} status={self.status}>"
