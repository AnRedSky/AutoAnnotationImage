"""
Segmentation Prediction (v2.0.0 图像分割)
=========================================

封装推理流程:
- 加载 state_dict 字节流
- 对单张图 (或多张) 推理, 输出 P-mode PIL 索引图 (像素值 = 类别索引)
- 索引图保存到 storage_service, mask_path 返回
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Dict, List, Sequence

import torch
import torch.nn as nn
from PIL import Image as PILImage
from torchvision import transforms

from app.services.storage_service import storage_service


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


def predict_to_mask_image(
    model: nn.Module,
    image_paths: Sequence[str],
    crop_size: int = 256,
    device: str = "cpu",
) -> Dict[str, PILImage]:
    """
    对一组图片跑推理, 返回 {abs_path: PIL.Image (P-mode)}.

    注: 输出 mask 与原图同尺寸 (PIL.Image 已在原尺寸上重采样).
    """
    tf = transforms.Compose([
        transforms.Resize((crop_size, crop_size)),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=[0.485, 0.456, 0.406],
            std=[0.229, 0.224, 0.225],
        ),
    ])
    out: Dict[str, PILImage] = {}
    model.eval()
    with torch.no_grad():
        for p in image_paths:
            pil = PILImage.open(p).convert("RGB")
            orig_w, orig_h = pil.size
            t = tf(pil).unsqueeze(0).to(device)
            logits = model(t)["out"]  # (1, C, H, W)
            pred = logits.argmax(dim=1).squeeze(0).cpu()  # (H, W) int64
            mask_pil = PILImage.fromarray(pred.numpy().astype("uint8"), mode="L")
            # 还原到原图尺寸 (nearest 保持索引值)
            mask_pil = mask_pil.resize(
                (orig_w, orig_h), resample=PILImage.NEAREST,
            ).convert("P")
            # 简单调色板: 256 色
            pal = [i % 256 for i in range(768)]
            mask_pil.putpalette(pal)
            out[p] = mask_pil
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
