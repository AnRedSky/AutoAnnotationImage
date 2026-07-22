"""
v2.5.15 P1-7 / D-1 测试: 图像分割训练模块
==========================================
覆盖 4 条用例:

 1. test_compute_miou_perfect           _compute_mIoU 全对 -> 1.0
 2. test_compute_miou_wrong             _compute_mIoU 全错 -> 0.0
 3. test_train_segmentation_empty_dataset  空数据 -> ValueError
 4. test_train_segmentation_mock_model  mock _build_model, 验证返回字段
"""
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
import torch.nn as nn
from PIL import Image as PILImage

from app.ml.segmentation.seg_train import (
    _compute_mIoU,
    _build_model,
    train_segmentation,
)


# ============== 工具 ==============

def _make_pair(tmp_path, w: int = 32, h: int = 32, label_value: int = 0):
    """构造 (image_path, mask_path) 一对"""
    img = PILImage.new("RGB", (w, h), color=(100, 150, 200))
    img_path = tmp_path / f"img_{label_value}.png"
    img.save(img_path)

    mask = PILImage.new("P", (w, h), color=label_value)
    mask_path = tmp_path / f"mask_{label_value}.png"
    mask.save(mask_path)
    return str(img_path), str(mask_path)


class _TinyModel(nn.Module):
    """极简 mock segmentation 模型, forward 返回 {out: (B, C, H, W)}

    使用 1x1 Conv 让输出有 grad_fn, 满足 loss.backward() 要求
    """
    def __init__(self, num_classes: int = 3, h: int = 32, w: int = 32):
        super().__init__()
        self.num_classes = num_classes
        self._h = h
        self._w = w
        # 1x1 Conv: 输入 3 通道 -> 输出 num_classes 通道
        # 让 output 来自 nn.Parameter 链, 保证有 grad_fn
        self.conv = nn.Conv2d(3, num_classes, kernel_size=1)
        # 偏置初始化让 class 0 权重最大 -> argmax = 0 (可复现)
        with torch.no_grad():
            self.conv.weight.zero_()
            self.conv.bias.zero_()
            self.conv.bias[0] = 5.0  # class 0 优势

    def forward(self, x):
        return {"out": self.conv(x)}

    def train(self, mode=True):
        self._mode = "train" if mode else "eval"
        return self

    def eval(self):
        self.train(False)
        return self

    def to(self, device):
        return self


# ============== 1. _compute_mIoU 全对 ==============

def test_compute_miou_perfect():
    """pred == target -> mIoU=1.0, pix_acc=1.0"""
    target = torch.tensor([[[0, 1, 2], [0, 1, 2]]])  # (1, H, W)
    # 构造 logits 让 argmax == target
    logits = torch.full((1, 3, 2, 3), -10.0)
    logits[0, 0, 0, 0] = 10.0
    logits[0, 0, 1, 0] = 10.0
    logits[0, 1, 0, 1] = 10.0
    logits[0, 1, 1, 1] = 10.0
    logits[0, 2, 0, 2] = 10.0
    logits[0, 2, 1, 2] = 10.0
    miou, pix_acc, n_valid = _compute_mIoU(logits, target, num_classes=3)
    assert miou == pytest.approx(1.0, abs=1e-6)
    assert pix_acc == pytest.approx(1.0, abs=1e-6)
    assert n_valid == 3


# ============== 2. _compute_mIoU 全错 ==============

def test_compute_miou_wrong():
    """pred 全部 = 0, target = 1 -> class 1 完全错 (IoU=0)"""
    target = torch.tensor([[[1, 1, 1]]])  # (1, 1, 3)
    logits = torch.zeros(1, 3, 1, 3)  # argmax = 0
    miou, pix_acc, _ = _compute_mIoU(logits, target, num_classes=3)
    # class 0: pred 有, target 没有 -> union > 0, inter = 0 -> iou = 0
    # class 1: pred 没有, target 有 -> union > 0, inter = 0 -> iou = 0
    # class 2: 都没有 -> 跳过
    # iou_sum = 0, valid_classes = 2 -> miou = 0
    assert miou == 0.0
    assert pix_acc == 0.0


# ============== 3. 空数据集 ==============

def test_train_segmentation_empty_dataset(tmp_path):
    """空 images/masks -> ValueError"""
    with pytest.raises(ValueError, match="数据集为空"):
        train_segmentation(
            images=[], masks=[],
            backbone="deeplabv3_resnet50", num_classes=2,
            epochs=1, batch_size=1, crop_size=32, device="cpu",
        )


# ============== 4. mock _build_model 训练 ==============

class _MockImageObj:
    """模拟 ORM Image 对象, 只提供 .storage_path 属性 (test 必需)"""
    def __init__(self, storage_path: str):
        self.storage_path = storage_path


class _MockMaskObj:
    """模拟 ORM SegmentationMask 对象, 只提供 .mask_path 属性 (test 必需)"""
    def __init__(self, mask_path: str):
        self.mask_path = mask_path


def test_train_segmentation_mock_model(tmp_path):
    """mock _build_model, 验证返回字段 + 进度回调"""
    img1_p, mask1_p = _make_pair(tmp_path, 32, 32, 0)
    img2_p, mask2_p = _make_pair(tmp_path, 32, 32, 1)
    # 包装成 mock ORM 对象 (SegmentationPairDataset 取 .storage_path / .mask_path)
    images = [_MockImageObj(img1_p), _MockImageObj(img2_p)]
    masks = [_MockMaskObj(mask1_p), _MockMaskObj(mask2_p)]

    progress_calls: list = []

    def _cb(stage, current, total, info):
        progress_calls.append((stage, current, total, info))

    with patch("app.ml.segmentation.seg_train._build_model") as mock_build:
        mock_build.return_value = _TinyModel(num_classes=3, h=32, w=32)
        out = train_segmentation(
            images=images, masks=masks,
            backbone="deeplabv3_resnet50", num_classes=3,
            epochs=2, batch_size=2, crop_size=32, device="cpu",
            progress_cb=_cb,
        )

    # 返回字段
    assert "best_miou" in out
    assert "best_pix_acc" in out
    assert out["epochs_trained"] == 2
    assert "duration_seconds" in out
    assert "state_dict_bytes" in out
    # state_dict 应是 bytes (内存序列化)
    assert isinstance(out["state_dict_bytes"], (bytes, type(None)))

    # 进度回调: start + 2 epoch + done = 4 次
    assert len(progress_calls) == 4
    assert progress_calls[0][0] == "train.start"
    assert progress_calls[-1][0] == "train.done"
