"""
Annotation Log ORM Model (审计用)

v2.5.15 P0-2 修复:
- action 枚举扩展: 增加 auto_annotate_pretrained / auto_annotate_finetuned
  (之前只有 ai_predict/confirm/correct/reject, 写新值会 ValueError 越界)
- 新增 payload JSON 字段: 存额外审计信息 (模型名/box数 等),
  避免在 BBoxAnnotation 等关联表加额外字段
"""
from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.database import Base


class AnnotationLog(Base):
    __tablename__ = "annotation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("image.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(
        # v2.5.15: 扩展枚举, 支持 auto-annotate 两类场景
        Enum(
            "ai_predict", "confirm", "correct", "reject",
            "auto_annotate_pretrained", "auto_annotate_finetuned",
            name="annotation_action",
        ),
        nullable=False
    )
    from_label_id: Mapped[int] = mapped_column(Integer, nullable=True)
    to_label_id: Mapped[int] = mapped_column(Integer, nullable=True)
    time_spent_ms: Mapped[int] = mapped_column(Integer, default=0)
    # v2.5.15 新增: JSON 字段, 存额外审计上下文 (模型名/box数/推理时间等)
    # 默认 None, 向后兼容
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="annotations")
