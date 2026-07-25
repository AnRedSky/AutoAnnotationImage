"""
Tasks API Package (Stage 2.5-2.8 完整迁移)
========================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/* 迁入 tasks 应用
**v3.0.0 Stage 2.8 完成**: 全部 9 个 API 路由已完整迁入
"""
# Stage 2.5-2.8 完整迁移
from app.tasks.api.dataset import router as dataset_router
from app.tasks.api.image import router as image_router
from app.tasks.api.training import router as training_router
from app.tasks.api.model import router as model_router
from app.tasks.api.auto_annotate import router as auto_annotate_router
from app.tasks.api.export import router as export_router
from app.tasks.api.detection import router as detection_router
from app.tasks.api.segmentation import router as segmentation_router
from app.tasks.api.files import router as files_router


# 命名导出 (供外部 import)
dataset = dataset_router  # type: ignore
image = image_router  # type: ignore
training = training_router  # type: ignore
model = model_router  # type: ignore
auto_annotate = auto_annotate_router  # type: ignore
export = export_router  # type: ignore
detection = detection_router  # type: ignore
segmentation = segmentation_router  # type: ignore
files = files_router  # type: ignore


__all__ = [
    "dataset", "image", "training", "model",
    "auto_annotate", "export", "detection", "segmentation", "files",
    "dataset_router", "image_router", "training_router", "model_router",
    "auto_annotate_router", "export_router", "detection_router", "segmentation_router", "files_router",
]
