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


# ================== 不合格标记原因 ==================

class RejectReason(str, Enum):
    """图片不合格标记的预设原因

    存储于 Image.reject_reason 字段;
    "其他" 场景的自定义文本存 AnnotationLog.payload.custom_text
    """
    BLURRY: Final[str] = "blurry"              # 图片模糊
    WRONG_CATEGORY: Final[str] = "wrong_category"  # 类别错误
    DUPLICATE: Final[str] = "duplicate"        # 重复图片
    OUT_OF_SCOPE: Final[str] = "out_of_scope"  # 非本数据集类别
    VIOLATION: Final[str] = "violation"        # 内容违规
    OTHER: Final[str] = "other"                # 其他 (需填自定义文本)


REJECT_REASON_VALUES: Final[Tuple[str, ...]] = tuple(
    r.value for r in RejectReason
)

# 中文标签映射 (供 API 返回 / 前端展示)
REJECT_REASON_LABELS: Final[dict[str, str]] = {
    RejectReason.BLURRY.value: "图片模糊",
    RejectReason.WRONG_CATEGORY.value: "类别错误",
    RejectReason.DUPLICATE.value: "重复图片",
    RejectReason.OUT_OF_SCOPE.value: "非本数据集类别",
    RejectReason.VIOLATION.value: "内容违规",
    RejectReason.OTHER.value: "其他",
}


# ================== 图片质量标记 ==================

# Image.quality_flag 字段的取值 (正交于 Image.status, 不影响状态机)
IMAGE_QUALITY_UNQUALIFIED: Final[str] = "unqualified"


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
