"""任务类型 / 标注来源 / 训练状态的集中枚举

v2.0.0 新增: 跨 API / ORM / 前端共用的字符串常量, 避免散落字面量

v3.0.0 迁移: 从 app.schemas.enums 迁入 app.common.enums (Phase 1.2)
"""
from __future__ import annotations

from enum import Enum
from typing import Final, Tuple


# ================== 任务类型 ==================

class TaskType(str, Enum):
    """数据集 / 模型 / 训练任务 3 种任务类型

    后端 ORM 已有 Enum('classification','detection','segmentation'),
    此处用 str 混入, 可与 Pydantic / SQLAlchemy 互通.
    """
    CLASSIFICATION: Final[str] = "classification"  # 图像分类 (v1.0 已有)
    DETECTION: Final[str] = "detection"             # 目标检测 (v2.0 新增)
    SEGMENTATION: Final[str] = "segmentation"       # 图像分割 (v2.0 新增)


# 任务类型元组, 用于 ORM Enum 与 Pydantic Literal 校验
TASK_TYPE_VALUES: Final[Tuple[str, ...]] = (
    TaskType.CLASSIFICATION.value,
    TaskType.DETECTION.value,
    TaskType.SEGMENTATION.value,
)

# 各任务类型的默认 base model (前端 TrainingParamsForm 推选用)
TASK_TYPE_DEFAULT_BASE_MODEL: Final[dict[str, str]] = {
    TaskType.CLASSIFICATION.value: "efficientnet_b0",
    TaskType.DETECTION.value: "yolov8n",
    TaskType.SEGMENTATION.value: "deeplabv3_resnet50",
}


# ================== 标注来源 ==================

class AnnotationSource(str, Enum):
    """BBox / Mask 标注的来源, 用于审计和筛选

    与 image.status 关系:
    - ai: AI 推理写入 (status=ai_labeled)
    - human: 人工标注 (status=human_confirmed)
    - human_corrected: 人工修正 (status=human_corrected)
    """
    AI: Final[str] = "ai"
    HUMAN: Final[str] = "human"
    HUMAN_CORRECTED: Final[str] = "human_corrected"


ANNOTATION_SOURCE_VALUES: Final[Tuple[str, ...]] = tuple(
    s.value for s in AnnotationSource
)


# ================== 检测 / 分割 训练任务状态 ==================

class DetectionTrainState(str, Enum):
    """检测 / 分割训练任务状态, 复用 Celery 状态约定

    与 classification TrainingJob.state 共用同一组枚举值
    """
    PENDING: Final[str] = "PENDING"
    PROGRESS: Final[str] = "PROGRESS"
    SUCCESS: Final[str] = "SUCCESS"
    FAILURE: Final[str] = "FAILURE"
    REVOKED: Final[str] = "REVOKED"
    PAUSED: Final[str] = "PAUSED"


DETECTION_TRAIN_STATE_VALUES: Final[Tuple[str, ...]] = tuple(
    s.value for s in DetectionTrainState
)


# ================== 工具函数 ==================

def is_valid_task_type(value: str | None) -> bool:
    """校验字符串是否为合法的 task_type (兼容 null / 空)"""
    if not value:
        return False
    return value in TASK_TYPE_VALUES


def normalize_task_type(value: str | None, default: str = TaskType.CLASSIFICATION.value) -> str:
    """兜底归一化: None / 空 / 非法 → default"""
    if not value:
        return default
    return value if value in TASK_TYPE_VALUES else default
