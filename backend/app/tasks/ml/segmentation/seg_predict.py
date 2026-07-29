"""
Segmentation Prediction (v2.0.0 图像分割, v2.5.46 +预训练支持, v3.1.0 +批量推理)
====================================================================

封装推理流程:
- 加载 state_dict 字节流
- 对单张图 (或多张) 推理, 输出 P-mode PIL 索引图 (像素值 = 类别索引)
- 索引图保存到 storage_service, mask_path 返回

v2.5.46 新增:
- load_pretrained_torchvision: 加载 torchvision.models.segmentation.* 预训练模型
  (COCO 21 类, weights='DEFAULT'), 用于「基础预标注」与「无 fine-tune 时的测评」
- predict_to_mask_image_with_conf: 复用 predict_to_mask_image 的循环,
  额外返回每张图 softmax 最大值作为「置信度代理」, 供测评和预标注判定是否落标

v3.1.0 Phase W4.3 新增:
- predict_to_mask_image / predict_to_mask_image_with_conf 改为分批 forward,
  取代逐张 unsqueeze(0). batch_size 默认取 settings.SEG_INFERENCE_BATCH_SIZE,
  调用方显式传 batch_size=1 可退回逐张行为 (向后兼容老测试).
"""
from __future__ import annotations

import io
import logging
from typing import Dict, List, Optional, Sequence, Tuple, Union

import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image as PILImage
from torchvision import transforms

from app.common.storage.storage_service import storage_service
from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_model_for_predict(backbone: str, num_classes: int) -> nn.Module:
    import torchvision
    if backbone == "deeplabv3_resnet101":
        # 推理时不下载预训练权重
        return torchvision.models.segmentation.deeplabv3_resnet101(
            weights=None, num_classes=num_classes,
        )
    if backbone == "fcn_resnet50":
        return torchvision.models.segmentation.fcn_resnet50(
            weights=None, num_classes=num_classes,
        )
    return torchvision.models.segmentation.deeplabv3_resnet50(
        weights=None, num_classes=num_classes,
    )


def load_model(
    state_dict_bytes: bytes,
    backbone: str = "deeplabv3_resnet50",
    num_classes: int = 2,
    device: str = "cpu",
) -> nn.Module:
    """从 state_dict 字节加载推理模型"""
    model = _build_model_for_predict(backbone, num_classes)
    sd = torch.load(io.BytesIO(state_dict_bytes), map_location=device)
    model.load_state_dict(sd)
    model.to(device)
    model.eval()
    return model


def _build_inference_transform(crop_size: int) -> transforms.Compose:
    return transforms.Compose([
        transforms.Resize((crop_size, crop_size)),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=[0.485, 0.456, 0.406],
            std=[0.229, 0.224, 0.225],
        ),
    ])


def _make_index_mask_pil(
    pred: torch.Tensor, orig_w: int, orig_h: int,
) -> PILImage:
    """把 (H, W) int64 argmax → P-mode PIL, 还原到原图尺寸."""
    mask_pil = PILImage.fromarray(pred.cpu().numpy().astype("uint8"), mode="L")
    mask_pil = mask_pil.resize(
        (orig_w, orig_h), resample=PILImage.NEAREST,
    ).convert("P")
    # 简单调色板: 256 色
    pal = [i % 256 for i in range(768)]
    mask_pil.putpalette(pal)
    return mask_pil


def _resolve_batch_size(explicit: Optional[int]) -> int:
    """batch_size 优先级: 显式参数 > settings.SEG_INFERENCE_BATCH_SIZE > 1.

    settings 默认 0 → 退回 1 (保持单张语义, 100% 向后兼容).

    Args:
        explicit: 调用方显式传入. None 时用 settings; <=0 时也退回 settings.

    Returns:
        >=1 的 batch size.
    """
    if explicit is not None and explicit > 0:
        return int(explicit)
    cfg = int(getattr(settings, "SEG_INFERENCE_BATCH_SIZE", 1) or 1)
    return max(1, cfg)


def predict_to_mask_image(
    model: nn.Module,
    image_paths: Sequence[str],
    crop_size: int = 256,
    device: str = "cpu",
    batch_size: Optional[int] = None,
) -> Dict[str, PILImage]:
    """对一组图片跑推理, 返回 {abs_path: PIL.Image (P-mode)}.

    v3.1.0 Phase W4.3: 逐张 unsqueeze(0) 改为分批 torch.stack + batch forward.
    默认 batch_size 取 settings.SEG_INFERENCE_BATCH_SIZE (env=SEG_INFERENCE_BATCH_SIZE,
    默认 4). 调用方传 batch_size=1 可保留旧单张行为; 传 batch_size=N (N>0) 强制批大小.

    行为兼容保证 (与旧实现逐位等价):
    - 同一张图同一模型: 输出 mask 像素值相同 (argmax 不引入随机性)
    - 输出 PIL.Image mode=P, size=(orig_w, orig_h), 调色板 [0..767] 同样填充
    - 空输入返回空 dict
    """
    bs = _resolve_batch_size(batch_size)
    tf = _build_inference_transform(crop_size)
    paths = list(image_paths)
    out: Dict[str, PILImage] = {}

    if not paths:
        return out

    model.eval()
    with torch.no_grad():
        if bs == 1:
            # 单张路径: 保留旧实现完全一致的 tensor 构造顺序 (逐张 unsqueeze(0)),
            # 数值与新批路径在 bs=1 时逐位等价, 仅保留以防用户强制 bs=1.
            for p in paths:
                pil = PILImage.open(p).convert("RGB")
                orig_w, orig_h = pil.size
                t = tf(pil).unsqueeze(0).to(device)
                logits = model(t)["out"]  # (1, C, H, W)
                pred = logits.argmax(dim=1).squeeze(0).cpu()  # (H, W) int64
                out[p] = _make_index_mask_pil(pred, orig_w, orig_h)
            return out

        # ---- 批量路径 ----
        # chunk 按 batch_size 切, 每 chunk 一次 forward; 模型 crop 在 crop_size×crop_size.
        # 不同尺寸的 orig 不影响模型输出, 仅在 _make_index_mask_pil 里 resize 还原.
        for start in range(0, len(paths), bs):
            chunk = paths[start:start + bs]
            tensors: List[torch.Tensor] = []
            orig_sizes: List[Tuple[int, int]] = []
            for p in chunk:
                pil = PILImage.open(p).convert("RGB")
                orig_sizes.append(pil.size)
                tensors.append(tf(pil))
            batch = torch.stack(tensors, dim=0).to(device)  # (B, 3, H, W)
            logits = model(batch)["out"]  # (B, C, H, W)
            for i, p in enumerate(chunk):
                pred = logits[i].argmax(dim=0).cpu()  # (H, W)
                orig_w, orig_h = orig_sizes[i]
                out[p] = _make_index_mask_pil(pred, orig_w, orig_h)
    return out


# v2.5.46: 加载 torchvision 预训练分割模型 (无 fine-tune 也能跑)
# - 用途: 标注工作台「基础模型」分支 (AnnotationToolbar 切到 segmentation + useFinetune=OFF)
#   / 测评端点无 fine-tune 时的回退
# - 输出: COCO 21 类 (含 background), 像素值 ∈ [0, 20]
# - 推理后归一化: 训练时类目不在 COCO 21 类的像素视为 background (=0), 由业务层按
#   项目类目做二次过滤
TORCHVISION_SEG_BACKBONES = {
    "fcn_resnet50",
    "deeplabv3_resnet50",
    "deeplabv3_resnet101",
}


def load_pretrained_torchvision(backbone: str, device: str = "cpu") -> nn.Module:
    """
    加载 torchvision 预训练分割模型 (COCO 21 类, weights='DEFAULT')
    支持 backbone: fcn_resnet50 / deeplabv3_resnet50 / deeplabv3_resnet101
    失败时抛 ValueError, 调用方按 auto_annotate.py:122-127 模式映射成 503
    """
    import torchvision.models.segmentation as tvm_seg

    if backbone not in TORCHVISION_SEG_BACKBONES:
        raise ValueError(
            f"Unsupported torchvision segmentation backbone: {backbone!r}. "
            f"Supported: {sorted(TORCHVISION_SEG_BACKBONES)}"
        )

    # weights='DEFAULT' = 最新可用预训练权重 (COCO 21 类, 含 background)
    # 不指定 num_classes, 保留预训练 head 的 21 类输出
    if backbone == "fcn_resnet50":
        model = tvm_seg.fcn_resnet50(weights="DEFAULT")
    elif backbone == "deeplabv3_resnet50":
        model = tvm_seg.deeplabv3_resnet50(weights="DEFAULT")
    else:  # deeplabv3_resnet101
        model = tvm_seg.deeplabv3_resnet101(weights="DEFAULT")
    model.to(device)
    model.eval()
    return model


def predict_to_mask_image_with_conf(
    model: nn.Module,
    image_paths: Sequence[str],
    crop_size: int = 256,
    device: str = "cpu",
    batch_size: Optional[int] = None,
) -> Dict[str, Tuple[PILImage, float]]:
    """
    v2.5.46 新增: 与 predict_to_mask_image 一致, 同时返回每张图的「置信度代理」

    v3.1.0 Phase W4.3: 同步切到批量推理, softmax 也对 batch 一次性算.

    返回:
      Dict[abs_path, (PIL P-mode 索引图, max_softmax_prob)]
      - max_softmax_prob: logits 在 channel 维做 softmax 后取 max 像素的值 ∈ [0, 1]
        作为「模型对该图预测有多确定」的代理; 测评/预标注据此判断是否落标
    """
    bs = _resolve_batch_size(batch_size)
    tf = _build_inference_transform(crop_size)
    paths = list(image_paths)
    out: Dict[str, Tuple[PILImage, float]] = {}

    if not paths:
        return out

    model.eval()
    with torch.no_grad():
        if bs == 1:
            for p in paths:
                pil = PILImage.open(p).convert("RGB")
                orig_w, orig_h = pil.size
                t = tf(pil).unsqueeze(0).to(device)
                logits = model(t)["out"]  # (1, C, H, W)
                probs = F.softmax(logits, dim=1)
                max_softmax = float(probs.max().item())
                pred = logits.argmax(dim=1).squeeze(0).cpu()  # (H, W) int64
                mask_pil = _make_index_mask_pil(pred, orig_w, orig_h)
                out[p] = (mask_pil, max_softmax)
            return out

        # ---- 批量路径 ----
        for start in range(0, len(paths), bs):
            chunk = paths[start:start + bs]
            tensors: List[torch.Tensor] = []
            orig_sizes: List[Tuple[int, int]] = []
            for p in chunk:
                pil = PILImage.open(p).convert("RGB")
                orig_sizes.append(pil.size)
                tensors.append(tf(pil))
            batch = torch.stack(tensors, dim=0).to(device)
            logits = model(batch)["out"]  # (B, C, H, W)
            probs = F.softmax(logits, dim=1)  # (B, C, H, W)
            for i, p in enumerate(chunk):
                max_softmax = float(probs[i].max().item())
                pred = logits[i].argmax(dim=0).cpu()
                orig_w, orig_h = orig_sizes[i]
                mask_pil = _make_index_mask_pil(pred, orig_w, orig_h)
                out[p] = (mask_pil, max_softmax)
    return out


def save_mask_pil(
    pil_mask: PILImage, dataset_id: int, image_id: int,
) -> str:
    """
    保存 PIL mask 到 storage_service, 返回相对 storage_key
    """
    buf = io.BytesIO()
    pil_mask.save(buf, format="PNG")
    content = buf.getvalue()
    file_hash = storage_service.compute_hash(content)
    storage_key = storage_service.generate_key(
        dataset_id, f"mask_pred_{image_id}.png", file_hash,
    )
    if not storage_key.endswith(".png"):
        storage_key = f"{storage_key}.png"
    return storage_key
