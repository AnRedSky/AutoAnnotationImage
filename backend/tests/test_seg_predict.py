"""
v2.5.15 P1-7 / D-1 测试: 图像分割推理模块
==========================================
覆盖 5 条用例:

 1. test_load_model_creates_deeplabv3   _build_model_for_predict 选 backbone
 2. test_load_model_loads_state_dict    load_model 加载 state_dict 字节
 3. test_predict_to_mask_image_empty    空输入返回空 dict
 4. test_predict_to_mask_image_mock     mock 模型, 验证 mask 输出尺寸
 5. test_save_mask_pil_generates_key    save_mask_pil 返回 storage_key
"""
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import torch
import torch.nn as nn
from PIL import Image as PILImage

from app.tasks.ml.segmentation.seg_predict import (
    _build_model_for_predict,
    load_model,
    predict_to_mask_image,
    save_mask_pil,
)


# ============== 工具 ==============

def _make_png_bytes(w: int = 64, h: int = 64, color=(100, 150, 200)) -> bytes:
    img = PILImage.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _MockSegModel(nn.Module):
    """Mock torchvision segmentation 模型, forward 返回 dict 含 'out'"""
    def __init__(self, num_classes: int = 3, h: int = 32, w: int = 32):
        super().__init__()
        self.num_classes = num_classes
        self._h = h
        self._w = w

    def forward(self, x):
        # 返回 logits (B, C, H, W), class 0 全 0 像素, 1 右上, 2 左下
        b = x.shape[0]
        logits = torch.zeros(b, self.num_classes, self._h, self._w)
        logits[:, 1, :self._h // 2, self._w // 2:] = 10.0
        logits[:, 2, self._h // 2:, :self._w // 2] = 10.0
        return {"out": logits}

    def eval(self):
        return self


# ============== 1. _build_model_for_predict ==============

def test_build_model_for_predict_deeplabv3_resnet50():
    """默认 deeplabv3_resnet50"""
    with patch("torchvision.models.segmentation.deeplabv3_resnet50") as mock_fn:
        mock_fn.return_value = MagicMock()
        model = _build_model_for_predict("deeplabv3_resnet50", num_classes=3)
        assert mock_fn.called
        # weights=None, num_classes=3
        call_kwargs = mock_fn.call_args.kwargs
        assert call_kwargs.get("num_classes") == 3


def test_build_model_for_predict_deeplabv3_resnet101():
    """backbone=deeplabv3_resnet101"""
    with patch("torchvision.models.segmentation.deeplabv3_resnet101") as mock_fn:
        mock_fn.return_value = MagicMock()
        _build_model_for_predict("deeplabv3_resnet101", num_classes=2)
        assert mock_fn.called


def test_build_model_for_predict_fcn_resnet50():
    """backbone=fcn_resnet50"""
    with patch("torchvision.models.segmentation.fcn_resnet50") as mock_fn:
        mock_fn.return_value = MagicMock()
        _build_model_for_predict("fcn_resnet50", num_classes=4)
        assert mock_fn.called


# ============== 2. load_model 加载 state_dict ==============

def test_load_model_loads_state_dict():
    """load_model 用 state_dict bytes 加载到模型"""
    with patch("app.tasks.ml.segmentation.seg_predict._build_model_for_predict") as mock_build:
        mock_model = MagicMock()
        mock_build.return_value = mock_model
        # mock torch.load 返回假 state_dict
        with patch("app.tasks.ml.segmentation.seg_predict.torch.load") as mock_load:
            mock_load.return_value = {"layer.weight": torch.zeros(1)}
            out = load_model(b"fake_state_dict", backbone="deeplabv3_resnet50", num_classes=2)
    assert mock_model.load_state_dict.called
    assert mock_load.called
    assert mock_model.eval.called


# ============== 3. 空输入 ==============

def test_predict_to_mask_image_empty():
    """空 image_paths -> 空 dict"""
    model = _MockSegModel()
    out = predict_to_mask_image(model, [], crop_size=32, device="cpu")
    assert out == {}


# ============== 4. mock 模型推理 ==============

def test_predict_to_mask_image_mock(tmp_path):
    """mock 模型推理, 验证 mask 是 P-mode 索引图"""
    img1 = tmp_path / "a.png"
    img1.write_bytes(_make_png_bytes(48, 48, (100, 150, 200)))
    img2 = tmp_path / "b.png"
    img2.write_bytes(_make_png_bytes(64, 64, (200, 100, 50)))

    model = _MockSegModel(num_classes=3, h=32, w=32)
    out = predict_to_mask_image(model, [str(img1), str(img2)], crop_size=32, device="cpu")

    assert str(img1) in out
    assert str(img2) in out
    # mask 应还原到原图尺寸
    assert out[str(img1)].size == (48, 48)
    assert out[str(img2)].size == (64, 64)
    # P-mode
    assert out[str(img1)].mode == "P"


# ============== 5. save_mask_pil ==============

def test_save_mask_pil_generates_key():
    """save_mask_pil 返回 storage_key, 包含 datasets/{id}/ 路径, 以 .png 结尾

    注: storage_service.generate_key 是 hash-based (去重), 不保留原 filename,
        只保留扩展名. 因此这里只验证路径/扩展名, 不验证文件名.
    """
    pil = PILImage.new("P", (32, 32), color=0)
    key = save_mask_pil(pil, dataset_id=1, image_id=42)
    assert "datasets/1/" in key
    assert key.endswith(".png")
    # hash-based key 格式: datasets/{id}/{hash2}/{hash}.png → 4 段
    parts = key.split("/")
    assert len(parts) == 4, f"key 应为 datasets/{{id}}/{{hash2}}/{{hash}}.png 格式: {key}"
    assert parts[0] == "datasets"
    assert parts[1] == "1"
    assert len(parts[2]) == 2, f"hash2 段应 2 字符: {key}"
    # 最后段: {hash}.png → hash 64 字符 + .png
    last = parts[3]
    assert last.endswith(".png")
    assert len(last) == 64 + 4, f"最后段应 64 字符 hash + .png: {key}"
