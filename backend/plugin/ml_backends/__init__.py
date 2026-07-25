"""
ML Backends Subpackage
======================

机器学习框架后端插件. 提供统一的模型构建/训练/推理能力.

**Stage 4 新增 (框架 + 第一实现)**. 包含:
- `timm_classification` — timm 框架的图像分类 (默认, 已实现)
- (未来) `ultralytics_detection` — ultralytics YOLOv8 目标检测
- (未来) `torchvision_segmentation` — torchvision 语义分割
- (未来) `huggingface` — HuggingFace Transformers

**当前状态**:
- timm_classification 已是可用包装, 委托给 `app.tasks.ml.classification`
- 其他后端仅文档占位, Stage 5+ 逐步迁移

**使用方式**:
```python
from app.registry import PluginRegistry
backend = PluginRegistry.get("ml_backend", "timm_classification")
model = backend.build_model("efficientnet_b0", num_classes=10)
results = backend.predict(model, pil_image, top_k=5)
```

**自动发现**:
导入本包会触发 `timm_classification` 模块的 PluginRegistry.register() 调用.
"""
# 显式 import, 触发插件自动注册
from plugin.ml_backends.timm_classification import TimmClassificationPlugin  # noqa: E402, F401

__all__ = ["TimmClassificationPlugin"]
