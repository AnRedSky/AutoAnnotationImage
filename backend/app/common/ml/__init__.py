"""
app.common.ml — 跨应用 ML 工具

包含:
- ai_service: timm 模型加载 / 推理 (原 app.common.ml.ai_service)

依赖方向: app.common.ml 仅依赖 torch/timm/PIL, 不依赖任何业务层.
"""
from app.common.ml.ai_service import (
    ai_service,
    AIService,
    filter_predictions_to_categories,
)


__all__ = [
    "ai_service",
    "AIService",
    "filter_predictions_to_categories",
]
