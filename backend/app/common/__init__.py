"""
Common Package — 业务可复用组件 (横切关注点)
=========================================

包含:
- 业务枚举 (enums)
- 业务异常 (exceptions)
- 常量 (constants)
- 共享类型 (types)
- 抽象接口 (interfaces) — Stage 2 新增
- 事件总线 (events) — Stage 2 新增
- ORM 基类扩展 (base_model)

依赖方向: common 不依赖任何业务层, 仅依赖标准库和 pydantic.
"""
from app.common.interfaces import (
    AppInterface,
    PluginInterface,
    StorageInterface,
    MLBackendInterface,
    TaskQueueInterface,
    EventHandler,
)
from app.common.events import (
    DomainEvent,
    EventBus,
    EventPriority,
    EventNames,
)
from app.common.exceptions import (
    AppException,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    ConflictError,
    to_response_payload,
)
from app.common.enums import (
    TaskType,
    AnnotationSource,
    DetectionTrainState,
    TASK_TYPE_VALUES,
    TASK_TYPE_DEFAULT_BASE_MODEL,
    ANNOTATION_SOURCE_VALUES,
    DETECTION_TRAIN_STATE_VALUES,
    is_valid_task_type,
    normalize_task_type,
)

__all__ = [
    # 接口
    "AppInterface",
    "PluginInterface",
    "StorageInterface",
    "MLBackendInterface",
    "TaskQueueInterface",
    "EventHandler",
    # 事件
    "DomainEvent",
    "EventBus",
    "EventPriority",
    "EventNames",
    # 异常
    "AppException",
    "NotFoundError",
    "PermissionDeniedError",
    "ValidationError",
    "ConflictError",
    "to_response_payload",
    # 枚举
    "TaskType",
    "AnnotationSource",
    "DetectionTrainState",
    "TASK_TYPE_VALUES",
    "TASK_TYPE_DEFAULT_BASE_MODEL",
    "ANNOTATION_SOURCE_VALUES",
    "DETECTION_TRAIN_STATE_VALUES",
    "is_valid_task_type",
    "normalize_task_type",
]
