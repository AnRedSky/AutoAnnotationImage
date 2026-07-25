"""
Segmentation ML Package (v2.0.0 图像分割 / v3.0.0 Stage 2.6 完整迁移)
====================================================================

封装 torchvision 分割模型 (DeepLabV3+) 的训练/推理/数据加载.

- 不强制依赖 GPU, CPU 可跑 (适合中小规模验证场景)
- 训练函数接受 progress_cb 回调, 写 TrainingJob.progress
- 推理输出 PIL 索引图 (P-mode), 像素值 = 类别索引

**v3.0.0 Stage 2.6**: 内容从原 app.ml.segmentation 迁入, app.ml.segmentation 转为
兼容垫片. 在本 __init__ 中聚合导出常用 API, 简化 `from app.tasks.ml.segmentation import X`.
"""
from app.tasks.ml.segmentation.seg_dataset import (
    SegmentationPairDataset,
    collect_segmentation_pairs,
)
from app.tasks.ml.segmentation.seg_train import (
    train_segmentation,
)
from app.tasks.ml.segmentation.seg_predict import (
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
