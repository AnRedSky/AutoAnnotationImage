"""
兼容垫片 (Phase 2): 从 app.models.* 转发到 app.model.*
======================================================

历史路径, 新代码请直接 `from app.model.user import User`
本文件将在 Phase 2 收尾时删除 (参见 [3层架构重构执行计划.md])

注意: 这个 shim 通过 sys.modules alias 实现, 让 `import app.models.user` 自动
指向 `app.model.user`, 无需复制 9 个文件, 节省维护成本.
"""
import sys
import importlib

# 1) 暴露 app.model.* 命名空间下的所有 ORM 模型 + 查询函数
#    让 `from app.models.user import User` 解析为 `from app.model.user import User`
from app.model import (  # noqa: F401
    User, Dataset, Category, Image, AnnotationLog,
    ModelVersion, TrainingJob, BBoxAnnotation, SegmentationMask,
    IMAGE_STATUS_VALUES, IMAGE_STATUS_CONFIRMED,
    TRAIN_STATE_VALUES, TRAIN_TERMINAL_STATES,
)

# 2) 子模块别名: 让 `import app.models.user` 自动映射到 `app.model.user`
#    这样所有 `from app.models.X import Y` 都能找到
_model_submodules = (
    "user", "dataset", "category", "image", "annotation_log",
    "model_version", "training_job", "bbox_annotation", "segmentation_mask",
    # 查询函数子模块
    "user_queries", "dataset_queries", "image_queries", "category_queries",
    "training_queries", "model_version_queries", "annotation_log_queries",
    "bbox_annotation_queries", "segmentation_mask_queries",
)
for _name in _model_submodules:
    _full = f"app.model.{_name}"
    # 把 app.models.X alias 到 app.model.X
    sys.modules[f"app.models.{_name}"] = importlib.import_module(_full)

# 3) 自身也做 alias: `import app.models` 等价于 `import app.model`
sys.modules["app.models"] = importlib.import_module("app.model")
