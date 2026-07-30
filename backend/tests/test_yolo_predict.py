"""
v2.5.15 P1-7 / D-1 测试: YOLO 推理模块
========================================
覆盖 6 条用例, mock ultralytics.YOLO 避免实际下载权重:

 1. test_yolo_box_to_dict             YoloBox.to_dict 序列化
 2. test_predict_yolo_empty_inputs    空输入返回空 list
 3. test_predict_yolo_missing_weights 权重缺失抛 FileNotFoundError
 4. test_predict_yolo_mock_ultralytics mock YOLO.predict 拿到 boxes
 5. test_predict_image_grouped         分组返回 dict
 6. test_group_predictions_by_image    按图分组的边界
"""
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image as PILImage

from app.tasks.ml.detection.yolo_predict import (
    YoloBox,
    predict_yolo,
    predict_image_grouped,
    group_predictions_by_image,
)


# ============== 工具 ==============

def _make_png_bytes(w: int = 32, h: int = 32, color=(120, 100, 60)) -> bytes:
    img = PILImage.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mock_yolo_results(boxes_per_image: list) -> list:
    """构造 mock Results 列表, 每张图 N 个 box

    注意: MagicMock.__len__ 默认返回 0, 必须显式配置 __len__.return_value
          否则 yolo_predict 中 `len(r.boxes) == 0` 判断会跳过整个 image
    """
    out = []
    for n_boxes in boxes_per_image:
        r = MagicMock()
        if n_boxes == 0:
            r.boxes = None
        else:
            r.boxes = MagicMock()
            r.boxes.__len__.return_value = n_boxes  # 关键: 显式设置长度
            r.boxes.xyxyn = MagicMock()
            r.boxes.xyxyn.cpu.return_value.numpy.return_value = np.array(
                [[0.1, 0.2, 0.3, 0.4]] * n_boxes, dtype=np.float32,
            )
            r.boxes.conf = MagicMock()
            r.boxes.conf.cpu.return_value.numpy.return_value = np.array(
                [0.9] * n_boxes, dtype=np.float32,
            )
            r.boxes.cls = MagicMock()
            r.boxes.cls.cpu.return_value.numpy.return_value = np.array(
                list(range(n_boxes)), dtype=int,
            )
        out.append(r)
    return out


# ============== 1. YoloBox.to_dict ==============

def test_yolo_box_to_dict():
    """YoloBox.to_dict 输出字段完整"""
    box = YoloBox(
        class_index=2, x_min=0.1, y_min=0.2,
        x_max=0.5, y_max=0.6, confidence=0.95,
    )
    d = box.to_dict()
    assert d["class_index"] == 2
    assert d["x_min"] == 0.1
    assert d["y_min"] == 0.2
    assert d["x_max"] == 0.5
    assert d["y_max"] == 0.6
    assert d["confidence"] == 0.95


# ============== 2. 空输入 ==============

def test_predict_yolo_empty_inputs(tmp_path):
    """空 image_paths -> 空 list, 不调 YOLO"""
    weights = tmp_path / "w.pt"
    weights.write_bytes(b"fake")
    out = predict_yolo(str(weights), [])
    assert out == []


# ============== 3. 权重缺失 ==============

def test_predict_yolo_missing_weights(tmp_path):
    """权重文件不存在 -> FileNotFoundError"""
    with pytest.raises(FileNotFoundError, match="权重文件不存在"):
        predict_yolo("/nonexistent/path/w.pt", ["x.png"])


# ============== 4. mock YOLO.predict ==============

def test_predict_yolo_mock_ultralytics(tmp_path):
    """mock ultralytics.YOLO 拿到 boxes"""
    weights = tmp_path / "w.pt"
    weights.write_bytes(b"fake")
    img1 = tmp_path / "x1.png"
    img1.write_bytes(_make_png_bytes(32, 32, (120, 100, 60)))
    img2 = tmp_path / "x2.png"
    img2.write_bytes(_make_png_bytes(48, 48, (60, 200, 130)))

    with patch("ultralytics.YOLO") as MockYOLO:
        MockYOLO.return_value.predict.return_value = _mock_yolo_results([2, 3])
        out = predict_yolo(str(weights), [str(img1), str(img2)], conf_threshold=0.25)

    # 2 + 3 = 5 个 box
    assert len(out) == 5
    assert all(isinstance(b, YoloBox) for b in out)
    assert out[0].confidence == pytest.approx(0.9, abs=1e-4)


# ============== 5. predict_image_grouped ==============

def test_predict_image_grouped(tmp_path):
    """分组返回, 每图一个 key, 0 框图为空 list"""
    weights = tmp_path / "w.pt"
    weights.write_bytes(b"fake")
    img1 = tmp_path / "a.png"
    img1.write_bytes(_make_png_bytes(32, 32, (120, 100, 60)))
    img2 = tmp_path / "b.png"
    img2.write_bytes(_make_png_bytes(48, 48, (60, 200, 130)))

    with patch("ultralytics.YOLO") as MockYOLO:
        MockYOLO.return_value.predict.return_value = _mock_yolo_results([2, 0])
        out = predict_image_grouped(str(weights), [str(img1), str(img2)])

    assert str(img1) in out
    assert str(img2) in out
    assert len(out[str(img1)]) == 2
    assert out[str(img2)] == []  # 0 框


# ============== 6. group_predictions_by_image ==============

def test_group_predictions_by_image_empty():
    """group_predictions_by_image 输入空 list 返回空 dict"""
    out = group_predictions_by_image([], [])
    assert out == {}


def test_group_predictions_by_image_initializes_keys():
    """即使预测为空, 也要初始化所有 image_path 键"""
    paths = ["a.png", "b.png"]
    out = group_predictions_by_image(paths, [])
    assert set(out.keys()) == set(paths)
    assert all(v == [] for v in out.values())
