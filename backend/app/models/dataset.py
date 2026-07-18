"""
Dataset ORM Model
"""
from sqlalchemy import String, Integer, Text, DateTime, Enum, ForeignKey, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.database import Base


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
        Enum("draft", "annotating", "training", "done", name="dataset_status"),
        default="draft", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    owner = relationship("User", back_populates="datasets")
    categories = relationship("Category", back_populates="dataset", cascade="all, delete-orphan")
    images = relationship("Image", back_populates="dataset", cascade="all, delete-orphan")
