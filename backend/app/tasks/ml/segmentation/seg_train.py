"""
Segmentation Training (v2.0.0 图像分割)
========================================

封装 torchvision segmentation 模型 (DeepLabV3+ ResNet50) 的训练.

- num_classes 含背景: 0 = background
- CrossEntropyLoss + Adam
- mIoU 评估
- progress_cb(stage, current, total, info) 回调用于 TrainingJob 进度
- 缺省 CPU 训练
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Callable, Dict, Any, Optional, List, Tuple
from datetime import datetime

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

# v3.5.0: 训练控制信号 (pause/cancel 区分)
# 顶层 import 以确保类型注解 + 异常类在训练循环里可用
from app.tasks.workers.control_signals import SignalAction, TaskCanceled  # noqa: E402
from app.tasks.ml.classification import TrainingPaused  # noqa: E402


ProgressCallback = Optional[Callable[[str, int, int, str], None]]
# v3.5.0: pause_check 返回 SignalAction 枚举, 与 classification/yolo 对齐
PauseCheckCallback = Optional[Callable[[], SignalAction]]


def _build_model(backbone: str, num_classes: int) -> nn.Module:
    """
    构造 torchvision 分割模型
    - deeplabv3_resnet50 / deeplabv3_resnet101
    - 其它: 兜底 fcn_resnet50

    注:
      - 使用 weights_backbone="DEFAULT" 仅加载骨干网络 (ResNet) 的 ImageNet 预训练权重,
        不加载预训练分类器头 (Pascal VOC 21 类), 避免 num_classes 不匹配报错.
      - 分类器头按 num_classes 随机初始化, 适配自定义数据集.
    """
    import torchvision
    if backbone == "deeplabv3_resnet101":
        # 仅加载骨干预训练权重, 分类器按 num_classes 重建
        return torchvision.models.segmentation.deeplabv3_resnet101(
            weights=None, weights_backbone="DEFAULT", num_classes=num_classes,
        )
    if backbone == "fcn_resnet50":
        return torchvision.models.segmentation.fcn_resnet50(
            weights=None, weights_backbone="DEFAULT", num_classes=num_classes,
        )
    # default: deeplabv3_resnet50
    return torchvision.models.segmentation.deeplabv3_resnet50(
        weights=None, weights_backbone="DEFAULT", num_classes=num_classes,
    )


def _compute_mIoU(
    pred_logits: torch.Tensor, target: torch.Tensor, num_classes: int,
) -> Tuple[float, float, float]:
    """
    简单 mIoU + pixel accuracy 评估
    pred_logits: (B, C, H, W)  logits
    target:      (B, H, W)     真实类别索引
    """
    with torch.no_grad():
        pred = pred_logits.argmax(dim=1)  # (B, H, W)
        # 像素精度
        valid = (target >= 0) & (target < num_classes)
        correct = ((pred == target) & valid).sum().item()
        total = valid.sum().item()
        pix_acc = correct / total if total > 0 else 0.0
        # mIoU
        iou_sum = 0.0
        valid_classes = 0
        for c in range(num_classes):
            p_c = (pred == c) & valid
            t_c = (target == c) & valid
            inter = (p_c & t_c).sum().item()
            union = (p_c | t_c).sum().item()
            if union > 0:
                iou_sum += inter / union
                valid_classes += 1
        miou = iou_sum / valid_classes if valid_classes > 0 else 0.0
    return miou, pix_acc, float(valid_classes)


def train_segmentation(
    images,
    masks,
    backbone: str = "deeplabv3_resnet50",
    num_classes: int = 2,
    epochs: int = 5,
    batch_size: int = 4,
    learning_rate: float = 1e-4,
    crop_size: int = 256,
    device: str = "cpu",
    progress_cb: ProgressCallback = None,
    pause_check: PauseCheckCallback = None,
) -> Dict[str, Any]:
    """
    训练分割模型, 返回 dict {
        best_miou, best_pix_acc, epochs_trained, duration_seconds,
        best_state_dict: bytes (state_dict 序列化, 用于 ModelVersion 落盘),
    }

    注: 缺省 CPU 训练, 生产部署建议 GPU.

    Args:
        pause_check (v3.5.0): 暂停/取消检查回调, 返回 SignalAction 枚举
            - SignalAction.CONTINUE: 继续训练
            - SignalAction.PAUSE:    抛 TrainingPaused (worker 写 PAUSED)
            - SignalAction.CANCEL:   抛 TaskCanceled (worker 写 CANCELED)
            检查点: 每个 epoch 起点 (避免打断 DataLoader 迭代器)
    """
    from .seg_dataset import SegmentationPairDataset

    started = datetime.utcnow()
    ds = SegmentationPairDataset(
        images=images, masks=masks, crop_size=crop_size,
        num_classes=num_classes,
    )
    if len(ds) == 0:
        raise ValueError("数据集为空, 无可训练样本")

    loader = DataLoader(
        ds, batch_size=min(batch_size, len(ds)),
        shuffle=True, num_workers=0,
    )

    model = _build_model(backbone, num_classes)
    model.to(device)
    model.train()

    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    loss_fn = nn.CrossEntropyLoss(ignore_index=-1)

    best_miou = -1.0
    best_pix_acc = 0.0
    best_state: Optional[Dict[str, Any]] = None

    if progress_cb:
        progress_cb("train.start", 0, epochs, f"backbone={backbone} n={len(ds)}")

    for epoch in range(1, epochs + 1):
        # ---- 暂停/取消检查: 每个 epoch 起点 (避免打断 DataLoader 迭代器) ----
        # v3.5.0: pause_check 返回 SignalAction 枚举, 区分 pause 与 cancel
        if pause_check is not None:
            try:
                action = pause_check()
            except Exception as e:
                # pause_check 自身异常不应阻塞训练, 仅记日志并视为 CONTINUE
                logger.warning(f"seg pause_check 调用异常: {e!r}, 视为 CONTINUE")
                action = SignalAction.CONTINUE
            if action == SignalAction.CANCEL:
                raise TaskCanceled(epoch=epoch, total_epochs=epochs)
            if action == SignalAction.PAUSE:
                raise TrainingPaused(epoch=epoch, total_epochs=epochs)

        epoch_loss = 0.0
        n_batches = 0
        for batch_idx, (imgs, tgts) in enumerate(loader, start=1):
            imgs = imgs.to(device)
            tgts = tgts.to(device)
            optimizer.zero_grad()
            out = model(imgs)["out"]  # (B, C, H, W)
            loss = loss_fn(out, tgts)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1
        avg_loss = epoch_loss / max(1, n_batches)

        # 评估 (复用最后一 batch logits, 简化)
        model.eval()
        with torch.no_grad():
            miou, pix_acc, _ = _compute_mIoU(out, tgts, num_classes)
        model.train()

        if miou > best_miou:
            best_miou = miou
            best_pix_acc = pix_acc
            # 序列化 state_dict (内存, 不落盘)
            buf = io.BytesIO()
            torch.save(model.state_dict(), buf)
            best_state = buf.getvalue()

        if progress_cb:
            # v2.5.27: 第 5 参数 metrics 是结构化指标 dict, 用于 SSE 透传 + history 累积
            # 旧 4-arg 签名仍兼容 (调用方若不接受 metrics, 走 except 路径)
            metrics = {
                "epoch": epoch,
                "train_loss": avg_loss,
                "val_loss": avg_loss,  # 当前未做 train/val 切分, 复用 train loss
                "miou": miou,
                "pixel_acc": pix_acc,
                "dice": (2.0 * miou / (1.0 + miou)) if miou > 0 else 0.0,
            }
            try:
                progress_cb(
                    "train.epoch", epoch, epochs,
                    f"loss={avg_loss:.4f} mIoU={miou:.4f} pixAcc={pix_acc:.4f}",
                    metrics,
                )
            except TypeError:
                # 兼容旧 4-arg 签名
                progress_cb(
                    "train.epoch", epoch, epochs,
                    f"loss={avg_loss:.4f} mIoU={miou:.4f} pixAcc={pix_acc:.4f}",
                )

    if progress_cb:
        progress_cb(
            "train.done", epochs, epochs,
            f"best_mIoU={best_miou:.4f}",
        )

    duration = (datetime.utcnow() - started).total_seconds()
    return {
        "best_miou": float(best_miou),
        "best_pix_acc": float(best_pix_acc),
        "epochs_trained": epochs,
        "duration_seconds": duration,
        "state_dict_bytes": best_state,
        "model": model,  # 训练后的 model 对象, 推理复用
    }
