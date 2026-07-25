"""
Constants (Common Layer)
========================

集中存放跨应用 / 跨模块的常量, 避免硬编码. 包括:
- 训练状态 (PENDING / RUNNING / SUCCESS / FAILURE / REVOKED)
- 数据集状态 (DRAFT / READY / DELETED)
- 模型状态 (TRAINING / ACTIVE / ARCHIVED)
- 标注状态 (UNCONFIRMED / CONFIRMED / CORRECTED)
- 通用限制 (默认分页大小, 文件上传大小, 训练超时常量)
- Redis key 前缀 (避免硬编码字符串)

**v3.0.0 Stage 3 新增**.

**使用方式**:
```python
from app.common.constants import TrainingState, DATASET_STATUS_READY

if job.status == TrainingState.SUCCESS:
    ...
```

**依赖方向**: constants 不依赖任何业务层, 仅依赖 common/enums.
"""
from enum import Enum

# ============== 训练状态 (与 Celery PENDING/PROGRESS/SUCCESS/FAILURE/REVOKED 对齐) ==============

class TrainingState(str, Enum):
    """训练任务状态机 (与 Celery state 一致)

    PENDING  : 任务入队, 等待 worker
    PROGRESS : 训练中, 周期性推送 meta
    SUCCESS  : 训练成功
    FAILURE  : 训练失败
    REVOKED  : 任务被取消 (含用户主动暂停)
    """
    PENDING = "PENDING"
    PROGRESS = "PROGRESS"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"

    @classmethod
    def terminal(cls) -> list[str]:
        """返回所有终态 (SUCCESS/FAILURE/REVOKED)"""
        return [cls.SUCCESS.value, cls.FAILURE.value, cls.REVOKED.value]

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        return state in cls.terminal()


# ============== 数据集状态 ==============

class DatasetStatus(str, Enum):
    """数据集状态"""
    DRAFT = "draft"        # 草稿, 上传中
    READY = "ready"        # 可用于训练
    DELETED = "deleted"    # 已删除 (软删除)


# ============== 模型版本状态 ==============

class ModelStatus(str, Enum):
    """模型版本状态"""
    TRAINING = "training"   # 训练中
    READY = "ready"         # 训练完成, 可用
    ACTIVE = "active"       # 当前激活
    ARCHIVED = "archived"   # 已归档


# ============== 标注状态 ==============

class AnnotationStatus(str, Enum):
    """标注状态"""
    UNCONFIRMED = "unconfirmed"  # AI 自动标注, 未确认
    CONFIRMED = "confirmed"      # 用户确认
    CORRECTED = "corrected"      # 用户修正过


# ============== 任务类型 ==============

class TaskTypeEnum(str, Enum):
    """任务类型 (与 common.enums.TaskType 对齐, 这里用不同名避免循环引用)"""
    CLASSIFICATION = "classification"
    DETECTION = "detection"
    SEGMENTATION = "segmentation"


# ============== 通用限制 ==============

# 分页
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# 文件上传
MAX_UPLOAD_SIZE_MB = 20
MAX_BATCH_UPLOAD_COUNT = 50

# 训练默认参数
DEFAULT_TRAIN_EPOCHS = 20
DEFAULT_TRAIN_BATCH_SIZE = 32
DEFAULT_TRAIN_LEARNING_RATE = 1e-4

# 训练时长限制
MAX_TRAIN_DURATION_HOURS = 24
MAX_TRAIN_INACTIVITY_MINUTES = 30  # 训练无进度上报超过此值视为卡死

# 自动标注
DEFAULT_AUTO_ANNOTATE_CONFIDENCE = 0.6
MIN_AUTO_ANNOTATE_CONFIDENCE = 0.1
MAX_AUTO_ANNOTATE_CONFIDENCE = 0.99

# ============== Redis key 前缀 ==============

# 避免硬编码字符串, 集中管理
REDIS_KEY_TRAIN_HISTORY = "train:history:{task_id}"      # 训练历史曲线
REDIS_KEY_TRAIN_PROGRESS = "train:progress:{task_id}"    # 训练进度快照
REDIS_KEY_TRAIN_ERROR = "train:error:{task_id}"          # 训练错误详情
REDIS_KEY_TRAIN_PAUSE = "train:pause:{task_id}"          # 训练暂停标志
REDIS_KEY_TRAIN_ACTIVE = "train:active:{user_id}"        # 用户当前活跃任务

# ============== HTTP / SSE 限制 ==============

SSE_MAX_DURATION_SECONDS = 30 * 60  # SSE 流最大持续时间 (30 分钟)
SSE_HEARTBEAT_INTERVAL_SECONDS = 15  # SSE 心跳间隔


__all__ = [
    # 状态机
    "TrainingState", "DatasetStatus", "ModelStatus", "AnnotationStatus", "TaskTypeEnum",
    # 限制
    "DEFAULT_PAGE_SIZE", "MAX_PAGE_SIZE",
    "MAX_UPLOAD_SIZE_MB", "MAX_BATCH_UPLOAD_COUNT",
    "DEFAULT_TRAIN_EPOCHS", "DEFAULT_TRAIN_BATCH_SIZE", "DEFAULT_TRAIN_LEARNING_RATE",
    "MAX_TRAIN_DURATION_HOURS", "MAX_TRAIN_INACTIVITY_MINUTES",
    "DEFAULT_AUTO_ANNOTATE_CONFIDENCE", "MIN_AUTO_ANNOTATE_CONFIDENCE", "MAX_AUTO_ANNOTATE_CONFIDENCE",
    # Redis keys
    "REDIS_KEY_TRAIN_HISTORY", "REDIS_KEY_TRAIN_PROGRESS", "REDIS_KEY_TRAIN_ERROR",
    "REDIS_KEY_TRAIN_PAUSE", "REDIS_KEY_TRAIN_ACTIVE",
    # SSE
    "SSE_MAX_DURATION_SECONDS", "SSE_HEARTBEAT_INTERVAL_SECONDS",
]
