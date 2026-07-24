"""
兼容垫片 (Phase 1.2): 从 app.schemas.enums 转发到 app.common.enums
==================================================================

历史路径, 新代码请直接 `from app.common.enums import ...`
本文件将在 Stage 2 完成后删除 (参见 [3层架构重构执行计划.md])
"""
# ruff: noqa: F401,F403
from app.common.enums import (  # noqa: F401
    TaskType,
    TASK_TYPE_VALUES,
    TASK_TYPE_DEFAULT_BASE_MODEL,
    AnnotationSource,
    ANNOTATION_SOURCE_VALUES,
    DetectionTrainState,
    DETECTION_TRAIN_STATE_VALUES,
    is_valid_task_type,
    normalize_task_type,
)
