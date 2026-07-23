"""
v2.5.15 P1-7 / D-1 测试: 图像分割训练模块
==========================================
覆盖 4 条用例:

 1. test_compute_miou_perfect           _compute_mIoU 全对 -> 1.0
 2. test_compute_miou_wrong             _compute_mIoU 全错 -> 0.0
 3. test_train_segmentation_empty_dataset  空数据 -> ValueError
 4. test_train_segmentation_mock_model  mock _build_model, 验证返回字段
 5. test_dataset_oob_pixels             mask 含越界像素 (e.g. 49) -> 标记 -1
"""
import io
import warnings
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
from app.ml.segmentation.seg_dataset import SegmentationPairDataset


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

    def _cb(stage, current, total, info, metrics=None):
        # v2.5.27: seg_train 现在传可选 5th 参数 metrics dict
        progress_calls.append((stage, current, total, info, metrics))

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

    # v2.5.27: 验证 train.epoch 回调现在带 metrics dict (loss/mIoU/pixel_acc/dice)
    epoch_calls = [c for c in progress_calls if c[0] == "train.epoch"]
    assert len(epoch_calls) == 2
    for stage, current, total, info, metrics in epoch_calls:
        assert isinstance(metrics, dict), \
            f"seg train.epoch 回调必须传 metrics dict, 实际: {type(metrics).__name__}"
        # 必含字段 (前端 detailChart / SSE 都需要)
        for k in ("epoch", "train_loss", "val_loss", "miou", "pixel_acc", "dice"):
            assert k in metrics, f"metrics 缺字段: {k}"
            assert isinstance(metrics[k], (int, float)), \
                f"metrics[{k}] 应为数值, 实际: {type(metrics[k]).__name__}"
        # dice 与 miou 关系: dice = 2*miou / (1+miou)
        if metrics["miou"] > 0:
            expected_dice = 2.0 * metrics["miou"] / (1.0 + metrics["miou"])
            assert abs(metrics["dice"] - expected_dice) < 1e-6, \
                f"dice 计算错误: 期望 {expected_dice}, 实际 {metrics['dice']}"


# ============== 5. mask 越界像素 (v2.5.16 回归测试) ==============

class _MockImageObj2:
    def __init__(self, storage_path: str):
        self.storage_path = storage_path


class _MockMaskObj2:
    def __init__(self, mask_path: str):
        self.mask_path = mask_path


def test_dataset_oob_pixels(tmp_path):
    """
    mask 像素值超出 [0, num_classes-1] (例如 49, 复现 v2.5.15 训练报错)
    期望:
      - 不崩溃
      - 越界像素被标 -1
      - 合法像素保留
      - 触发 RuntimeWarning
    """
    img_path, _ = _make_pair(tmp_path, 32, 32, 0)
    # 构造 mask: 主区域 0, 1 个越界像素 49, 1 个合法像素 2
    mask = PILImage.new("L", (32, 32), color=0)
    mask.putpixel((0, 0), 49)   # OOB: >= num_classes=4
    mask.putpixel((1, 1), 2)    # in-range
    mask_path = tmp_path / "mask_oob.png"
    mask.save(mask_path)

    images = [_MockImageObj2(img_path)]
    masks = [_MockMaskObj2(str(mask_path))]
    ds = SegmentationPairDataset(
        images=images, masks=masks, crop_size=32, num_classes=4,
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _, mask_t = ds[0]

    # 越界像素标 -1
    assert mask_t[0, 0].item() == -1
    # 合法像素保留
    assert mask_t[1, 1].item() == 2
    # 其余仍为 0
    assert (mask_t[2:, 2:] == 0).all()
    # 触发警告
    oob_warnings = [w for w in captured
                    if "越界像素" in str(w.message) and issubclass(w.category, RuntimeWarning)]
    assert len(oob_warnings) == 1, f"expected 1 OOB warning, got {len(oob_warnings)}"


def test_dataset_oob_no_param_safe(tmp_path):
    """
    不传 num_classes 时, 不做 OOB 检查, 行为与旧版完全一致
    (向后兼容, 防止影响旧调用方)
    """
    img_path, _ = _make_pair(tmp_path, 32, 32, 0)
    mask = PILImage.new("L", (32, 32), color=0)
    mask.putpixel((0, 0), 49)
    mask_path = tmp_path / "mask_oob2.png"
    mask.save(mask_path)

    images = [_MockImageObj2(img_path)]
    masks = [_MockMaskObj2(str(mask_path))]
    ds = SegmentationPairDataset(
        images=images, masks=masks, crop_size=32,  # num_classes 缺省
    )
    _, mask_t = ds[0]
    # 缺省 num_classes: 不干预, 保留 49 (向后兼容)
    assert mask_t[0, 0].item() == 49
