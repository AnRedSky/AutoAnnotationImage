"""
Model Version ORM Model
"""
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON, Boolean, Float, Enum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_model: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. efficientnet_b0
    dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dataset.id"), nullable=True
    )
    # v2.0.0: 任务类型 (classification / detection / segmentation)
    task_type: Mapped[str] = mapped_column(
        Enum("classification", "detection", "segmentation", name="model_task_type"),
        default="classification", nullable=False, index=True
    )
    num_classes: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str] = mapped_column(String(500), nullable=True)
    # classification 4 指标 (v1.0.0 已有)
    accuracy: Mapped[float] = mapped_column(Float, default=0)
    precision: Mapped[float] = mapped_column(Float, default=0)
    recall: Mapped[float] = mapped_column(Float, default=0)
    f1_score: Mapped[float] = mapped_column(Float, default=0)
    # v2.0.0: 检测专属指标 (mAP@0.5 / mAP@0.5:0.95)
    map_50: Mapped[float] = mapped_column(Float, nullable=True)
    map_50_95: Mapped[float] = mapped_column(Float, nullable=True)
    # v2.0.0: 分割专属指标 (mIoU / pixel accuracy / dice)
    miou: Mapped[float] = mapped_column(Float, nullable=True)
    pixel_accuracy: Mapped[float] = mapped_column(Float, nullable=True)
    dice_score: Mapped[float] = mapped_column(Float, nullable=True)
    # 训练曲线 / 评估结果 (task_type 不同, JSON 内容不同)
    training_log: Mapped[dict] = mapped_column(JSON, nullable=True)
    confusion_matrix: Mapped[list] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
