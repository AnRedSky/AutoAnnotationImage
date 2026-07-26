"""
ModelVersion ORM Model (Active Record) — app/tasks/model/
=======================================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/model_version.py 迁入 tasks 应用
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON, Boolean, Float, Enum
from sqlalchemy.orm import Mapped, mapped_column
from app.common.base_model import Base


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_model: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dataset.id"), nullable=True
    )
    task_type: Mapped[str] = mapped_column(
        Enum("classification", "detection", "segmentation", name="model_task_type"),
        default="classification", nullable=False, index=True
    )
    num_classes: Mapped[int] = mapped_column(Integer, default=0)
    # v3.0.0: 类别名称列表 (按训练时 label_idx 顺序), 供推理时重建索引→类别名映射
    # - 含虚拟类别 __unqualified__ 时, 推理预测该索引 → 自动标记不合格
    # - 旧模型为 NULL, 推理时 fallback 到 Category 表 sorted (向后兼容)
    class_names: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=True)
    accuracy: Mapped[float] = mapped_column(Float, default=0)
    precision: Mapped[float] = mapped_column(Float, default=0)
    recall: Mapped[float] = mapped_column(Float, default=0)
    f1_score: Mapped[float] = mapped_column(Float, default=0)
    map_50: Mapped[float] = mapped_column(Float, nullable=True)
    map_50_95: Mapped[float] = mapped_column(Float, nullable=True)
    miou: Mapped[float] = mapped_column(Float, nullable=True)
    pixel_accuracy: Mapped[float] = mapped_column(Float, nullable=True)
    dice_score: Mapped[float] = mapped_column(Float, nullable=True)
    training_log: Mapped[dict] = mapped_column(JSON, nullable=True)
    confusion_matrix: Mapped[list] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # ============== Active Record 业务方法 ==============

    def primary_metric_value(self) -> float:
        """返回主指标 (按 task_type 不同)
        - classification: accuracy
        - detection: mAP@0.5
        - segmentation: mIoU
        """
        if self.task_type == "detection":
            return self.map_50 or 0.0
        if self.task_type == "segmentation":
            return self.miou or 0.0
        return self.accuracy or 0.0

    def primary_metric_name(self) -> str:
        if self.task_type == "detection":
            return "mAP@0.5"
        if self.task_type == "segmentation":
            return "mIoU"
        return "accuracy"

    def activate(self) -> None:
        """标记为激活 (业务规则: 同 dataset 只能一个激活)"""
        self.is_active = True

    def deactivate(self) -> None:
        self.is_active = False

    def is_classification(self) -> bool:
        return self.task_type == "classification"

    def is_detection(self) -> bool:
        return self.task_type == "detection"

    def is_segmentation(self) -> bool:
        return self.task_type == "segmentation"

    def __repr__(self) -> str:
        return (
            f"<ModelVersion {self.id} {self.name!r} task={self.task_type} "
            f"{self.primary_metric_name()}={self.primary_metric_value():.4f} "
            f"active={self.is_active}>"
        )
