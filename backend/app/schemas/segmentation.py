"""Pydantic Schemas: Segmentation (v2.0.0 图像分割)

约定:
- Mask 物理存储: PNG 索引图 (P-mode), 像素值 = 类别索引 (0=背景)
- API 传输: 走 mask_path 引用, 不直传二进制
- 调色板: 前端按 Category.color 实时渲染
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class SegmentationMaskBase(BaseModel):
    """Mask 元数据, 二进制走 mask_path"""
    image_id: int
    width: int = Field(gt=0, description="mask 宽度 (像素)")
    height: int = Field(gt=0, description="mask 高度 (像素)")
    source: str = Field(default="human", description="ai / human / human_corrected")


class SegmentationMaskCreate(SegmentationMaskBase):
    """创建 / 更新单条 mask 记录

    mask_path 由后端在收到前端 base64 上传后写入磁盘, 客户端不传
    """
    mask_path: str = Field(description="相对 UPLOAD_DIR 的 PNG 路径")


class SegmentationMaskOut(SegmentationMaskBase):
    id: int
    mask_path: str
    annotated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # 关联的类别统计 (counts[category_id] = 像素数)
    category_pixel_counts: Optional[Dict[int, int]] = None

    model_config = ConfigDict(from_attributes=True)


class SegmentationTrainRequest(BaseModel):
    """启动分割训练请求

    backbone 选项: deeplabv3_resnet50 / deeplabv3_resnet101 / fcn_resnet50
    """
    dataset_id: int
    backbone: str = "deeplabv3_resnet50"
    model_name: str = ""
    epochs: int = 20
    batch_size: int = 8
    learning_rate: float = 1e-4
    crop_size: int = 256
    pretrained_model_path: str = ""

    # 关掉 Pydantic v2 默认的 model_ 命名空间保护, 避免 model_name/model_id 警告
    model_config = ConfigDict(protected_namespaces=())


class SegmentationTrainResponse(BaseModel):
    task_id: str
    celery_task_id: str
    job_id: int
    state: str
    message: str
