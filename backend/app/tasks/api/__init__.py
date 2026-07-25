"""
Tasks API Package (Stage 2.5 填充)
=================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/* 迁入 tasks 应用

**当前状态**:
- 已完整迁移: 无 (待 Stage 5 重构完成)
- 过渡引用: 大部分路由仍位于 app/api/*, 通过 re-export 提供
  app.tasks.api.<name>.router 访问入口, 供 main.py 集中挂载.
"""
# 过渡: re-export 老路径的 router, 让 app.tasks.api.<name> 可用
from app.api.dataset import router as dataset_router
from app.api.image import router as image_router
from app.api.training import router as training_router
from app.api.model import router as model_router
from app.api.auto_annotate import router as auto_annotate_router
from app.api.export import router as export_router
from app.api.detection import router as detection_router
from app.api.segmentation import router as segmentation_router
from app.api.files import router as files_router


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
