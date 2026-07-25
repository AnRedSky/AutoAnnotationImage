"""
兼容垫片 (Stage 2.6): segmentation ML 子包
==========================================

**v3.0.0 Stage 2.6 迁移**: 原 app.ml.segmentation 内容已迁入 app.tasks.ml.segmentation,
本文件 re-export 整包, 保持旧 import 路径可用.
"""
from app.tasks.ml.segmentation import *  # noqa: F401,F403
from app.tasks.ml.segmentation import (  # noqa: F401
    SegmentationPairDataset,
    collect_segmentation_pairs,
    train_segmentation,
    load_model,
    predict_to_mask_image,
    predict_to_mask_image_with_conf,
    save_mask_pil,
    load_pretrained_torchvision,
    TORCHVISION_SEG_BACKBONES,
)


__all__ = [
    "SegmentationPairDataset",
    "collect_segmentation_pairs",
    "train_segmentation",
    "load_model",
    "predict_to_mask_image",
    "predict_to_mask_image_with_conf",
    "save_mask_pil",
    "load_pretrained_torchvision",
    "TORCHVISION_SEG_BACKBONES",
]
