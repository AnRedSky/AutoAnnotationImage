"""
Common Package — 业务可复用组件 (横切关注点)
=========================================

包含:
- 业务枚举 (enums)
- 业务常量 (constants) — Stage 3 新增
- 业务异常 (exceptions)
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
# Stage 3 新增: 集中常量
from app.common.constants import (  # noqa: E402
    # 状态机
    TrainingState,
    DatasetStatus,
    ModelStatus,
    AnnotationStatus,
    TaskTypeEnum,
    # 限制
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_UPLOAD_SIZE_MB,
    MAX_BATCH_UPLOAD_COUNT,
    DEFAULT_TRAIN_EPOCHS,
    DEFAULT_TRAIN_BATCH_SIZE,
    DEFAULT_TRAIN_LEARNING_RATE,
    MAX_TRAIN_DURATION_HOURS,
    MAX_TRAIN_INACTIVITY_MINUTES,
    DEFAULT_AUTO_ANNOTATE_CONFIDENCE,
    MIN_AUTO_ANNOTATE_CONFIDENCE,
    MAX_AUTO_ANNOTATE_CONFIDENCE,
    # Redis keys
    REDIS_KEY_TRAIN_HISTORY,
    REDIS_KEY_TRAIN_PROGRESS,
    REDIS_KEY_TRAIN_ERROR,
    REDIS_KEY_TRAIN_PAUSE,
    REDIS_KEY_TRAIN_ACTIVE,
    # SSE
    SSE_MAX_DURATION_SECONDS,
    SSE_HEARTBEAT_INTERVAL_SECONDS,
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
    # 常量 (Stage 3)
    "TrainingState", "DatasetStatus", "ModelStatus", "AnnotationStatus", "TaskTypeEnum",
    "DEFAULT_PAGE_SIZE", "MAX_PAGE_SIZE",
    "MAX_UPLOAD_SIZE_MB", "MAX_BATCH_UPLOAD_COUNT",
    "DEFAULT_TRAIN_EPOCHS", "DEFAULT_TRAIN_BATCH_SIZE", "DEFAULT_TRAIN_LEARNING_RATE",
    "MAX_TRAIN_DURATION_HOURS", "MAX_TRAIN_INACTIVITY_MINUTES",
    "DEFAULT_AUTO_ANNOTATE_CONFIDENCE", "MIN_AUTO_ANNOTATE_CONFIDENCE", "MAX_AUTO_ANNOTATE_CONFIDENCE",
    "REDIS_KEY_TRAIN_HISTORY", "REDIS_KEY_TRAIN_PROGRESS", "REDIS_KEY_TRAIN_ERROR",
    "REDIS_KEY_TRAIN_PAUSE", "REDIS_KEY_TRAIN_ACTIVE",
    "SSE_MAX_DURATION_SECONDS", "SSE_HEARTBEAT_INTERVAL_SECONDS",
]
