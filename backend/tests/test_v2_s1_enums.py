"""v2.0.0 S1 测试: 任务类型枚举 + 共享工具"""
import pytest

from app.schemas.enums import (
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


def test_task_type_values():
    """3 种任务类型, 与前端 taskType.ts 一致"""
    assert TASK_TYPE_VALUES == ("classification", "detection", "segmentation")


def test_default_base_model_mapping():
    """每种任务类型有推荐 base model"""
    assert TASK_TYPE_DEFAULT_BASE_MODEL["classification"] == "efficientnet_b0"
    assert TASK_TYPE_DEFAULT_BASE_MODEL["detection"] == "yolov8n"
    assert TASK_TYPE_DEFAULT_BASE_MODEL["segmentation"] == "deeplabv3_resnet50"


def test_is_valid_task_type():
    assert is_valid_task_type("classification") is True
    assert is_valid_task_type("detection") is True
    assert is_valid_task_type("segmentation") is True
    assert is_valid_task_type("invalid") is False
    assert is_valid_task_type(None) is False
    assert is_valid_task_type("") is False


def test_normalize_task_type():
    # 合法值原样返回
    assert normalize_task_type("detection") == "detection"
    assert normalize_task_type("segmentation") == "segmentation"
    # 兜底: None / 空 / 非法 → default
    assert normalize_task_type(None) == "classification"
    assert normalize_task_type("") == "classification"
    assert normalize_task_type("invalid") == "classification"
    # 自定义 default
    assert normalize_task_type(None, default="detection") == "detection"


def test_annotation_source_values():
    assert set(ANNOTATION_SOURCE_VALUES) == {"ai", "human", "human_corrected"}


def test_detection_train_state_values():
    """6 个状态: PENDING / PROGRESS / SUCCESS / FAILURE / REVOKED / PAUSED"""
    assert set(DETECTION_TRAIN_STATE_VALUES) == {
        "PENDING", "PROGRESS", "SUCCESS", "FAILURE", "REVOKED", "PAUSED"
    }


def test_task_type_enum_str_mixin():
    """TaskType 继承 str, 可直接当字符串用 (与 Pydantic v2 兼容)"""
    assert TaskType.CLASSIFICATION == "classification"
    assert TaskType.DETECTION.value == "detection"
    assert TaskType.SEGMENTATION.value == "segmentation"
