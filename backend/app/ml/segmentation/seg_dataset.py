"""
Segmentation Dataset (v2.0.0 图像分割)
=======================================

职责:
- 从 ORM 读 Image + SegmentationMask
- 返回 (image_tensor, mask_tensor) pair, mask 像素值 = 类别索引
- 不强制下载预训练权重, transform 保持简单 (ToTensor + Resize)
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence, Tuple, List, Optional

from PIL import Image as PILImage
from torch.utils.data import Dataset
import torch
from torchvision import transforms

from app.models.image import Image as ImageModel
from app.models.segmentation_mask import SegmentationMask
from app.services.storage_service import storage_service


def _resolve_path(rel: str) -> Path:
    """rel 路径 -> 绝对路径 (相对 storage_service.base_dir)"""
    p = Path(rel)
    if p.is_absolute():
        return p
    return (Path(storage_service.base_dir) / rel).resolve()


def _build_transforms(crop_size: int = 256):
    """图像 transform: 缩放到 crop_size, ToTensor, 归一化 ImageNet mean/std"""
    img_tf = transforms.Compose([
        transforms.Resize((crop_size, crop_size)),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=[0.485, 0.456, 0.406],
            std=[0.229, 0.224, 0.225],
        ),
    ])
    mask_tf = transforms.Compose([
        transforms.Resize(
            (crop_size, crop_size), interpolation=transforms.InterpolationMode.NEAREST,
        ),
        transforms.PILToTensor(),  # 输出 (1, H, W) int64
    ])
    return img_tf, mask_tf


class SegmentationPairDataset(Dataset):
    """
    图像 + mask 配对数据集

    输入:
        images: Sequence[ImageModel]
        masks: Sequence[SegmentationMask]  (与 images 顺序一一对应, 长度相同)
        crop_size: 输出空间大小

    输出:
        __getitem__(i) -> (img_tensor (3, H, W) float32, mask_tensor (1, H, W) int64)
    """

    def __init__(
        self,
        images: Sequence[ImageModel],
        masks: Sequence[SegmentationMask],
        crop_size: int = 256,
    ):
        assert len(images) == len(masks), "images 与 masks 长度必须一致"
        self.images = list(images)
        self.masks = list(masks)
        self.crop_size = crop_size
        self.img_tf, self.mask_tf = _build_transforms(crop_size)

    def __len__(self) -> int:
        return len(self.images)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        img = self.images[idx]
        m = self.masks[idx]
        img_path = _resolve_path(img.storage_path)
        mask_path = _resolve_path(m.mask_path)
        pil_img = PILImage.open(img_path).convert("RGB")
        pil_mask = PILImage.open(mask_path)
        # mask 可能是 P 或 L, 都按 index 处理
        if pil_mask.mode not in ("P", "L"):
            pil_mask = pil_mask.convert("L")
        img_t = self.img_tf(pil_img)
        mask_t = self.mask_tf(pil_mask).squeeze(0).long()
        return img_t, mask_t


# ============== 异步辅助: 拉 image + mask 配对 ==============

async def collect_segmentation_pairs(
    db, dataset_id: int,
) -> Tuple[List[ImageModel], List[SegmentationMask]]:
    """
    拉取一个 dataset 下有 mask 的 (image, mask) 配对列表

    返回两个等长 list, 顺序一致; 没 mask 的图被过滤.
    """
    from sqlalchemy import select

    imgs = (await db.execute(
        select(ImageModel)
        .where(
            ImageModel.dataset_id == dataset_id,
            ImageModel.task_type == "segmentation",
        )
        .order_by(ImageModel.id.asc())
    )).scalars().all()

    img_ids = [im.id for im in imgs]
    if not img_ids:
        return [], []

    masks = (await db.execute(
        select(SegmentationMask).where(SegmentationMask.image_id.in_(img_ids))
    )).scalars().all()
    mask_by_img = {m.image_id: m for m in masks}

    paired_imgs: List[ImageModel] = []
    paired_masks: List[SegmentationMask] = []
    for im in imgs:
        m = mask_by_img.get(im.id)
        if m is not None:
            paired_imgs.append(im)
            paired_masks.append(m)
    return paired_imgs, paired_masks
