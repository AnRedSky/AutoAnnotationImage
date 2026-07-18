"""v2.0.0 S1 测试: 任务类型 Schema + BBox / Mask Schema 字段校验"""
import pytest
from pydantic import ValidationError

from app.schemas.detection import BBoxCreate, BBoxOut
from app.schemas.segmentation import SegmentationMaskCreate, SegmentationMaskOut


# ============== BBox Schema ==============

def test_bbox_create_valid():
    b = BBoxCreate(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.6, category_id=1)
    assert b.x_min == 0.1
    assert b.category_id == 1
    assert b.source == "human"  # default


def test_bbox_create_invalid_coord_range():
    """坐标超出 [0, 1] 报错"""
    with pytest.raises(ValidationError):
        BBoxCreate(x_min=-0.1, y_min=0.2, x_max=0.5, y_max=0.6)


def test_bbox_create_inverted_box():
    """x_max <= x_min 通过 Pydantic 字段校验, 但 validate_box() 兜底"""
    b = BBoxCreate(x_min=0.5, y_min=0.2, x_max=0.1, y_max=0.6)
    with pytest.raises(ValueError, match="x_max"):
        b.validate_box()


def test_bbox_create_zero_area():
    """y_max = y_min: 零高度框"""
    b = BBoxCreate(x_min=0.1, y_min=0.5, x_max=0.5, y_max=0.5)
    with pytest.raises(ValueError, match="y_max"):
        b.validate_box()


def test_bbox_create_confidence_range():
    """confidence 必须在 [0, 1]"""
    b = BBoxCreate(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.6, confidence=0.85)
    assert b.confidence == 0.85
    with pytest.raises(ValidationError):
        BBoxCreate(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.6, confidence=1.5)


# ============== Segmentation Schema ==============

def test_segmentation_mask_create_valid():
    m = SegmentationMaskCreate(
        image_id=1, mask_path="ds_1/masks/1.png",
        width=640, height=480,
    )
    assert m.width == 640
    assert m.height == 480
    assert m.source == "human"


def test_segmentation_mask_create_invalid_dim():
    """width / height 必须 > 0"""
    with pytest.raises(ValidationError):
        SegmentationMaskCreate(
            image_id=1, mask_path="x.png", width=0, height=480
        )
    with pytest.raises(ValidationError):
        SegmentationMaskCreate(
            image_id=1, mask_path="x.png", width=640, height=-1
        )
