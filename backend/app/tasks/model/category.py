"""
Category ORM Model — app/tasks/model/
====================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/category.py 迁入 tasks 应用
"""
from sqlalchemy import String, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.common.base_model import Base


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    color: Mapped[str] = mapped_column(String(20), default="#409EFF")
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    dataset = relationship("Dataset", back_populates="categories")

    def __repr__(self) -> str:
        return f"<Category {self.id} {self.name!r} (dataset={self.dataset_id})>"
