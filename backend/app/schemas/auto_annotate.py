"""Pydantic Schemas: Auto Annotate (v3.0.0 Phase 4 新增)

AI 预标注 / 自动标注的请求/响应 schema, 集中管理.
"""
from typing import Optional, List, Dict
from pydantic import BaseModel, ConfigDict, Field


class AutoAnnotateRequest(BaseModel):
    """AI 预标注请求 (classification 同步/异步入口)"""
    dataset_id: int
    model_name: str = Field(default="efficientnet_b0", description="timm 模型名")
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    async_mode: bool = Field(default=False, description="True 走 Celery, False 走同步")

    model_config = ConfigDict(protected_namespaces=())


class AutoAnnotateResponse(BaseModel):
    """AI 预标注响应"""
    total: int
    auto_labeled: int
    need_human: int
    no_match: int = 0
    avg_confidence: float
    threshold: float
    task_id: Optional[str] = None
    mode: str  # "sync" | "async"


class AutoLabelRequest(BaseModel):
    """标注工作台自动标注请求 (use_finetune 开关)"""
    dataset_id: int
    model_name: str = Field(default="efficientnet_b0", description="timm 模型名 (use_finetune=False)")
    model_id: Optional[int] = Field(default=None, description="指定 fine-tune ModelVersion.id")
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    use_finetune: bool = Field(default=True, description="True=项目微调, False=ImageNet 预训练")

    model_config = ConfigDict(protected_namespaces=())


class AutoLabelResponse(BaseModel):
    """标注工作台自动标注响应"""
    total: int
    auto_labeled: int
    need_human: int
    no_match: int = 0
    avg_confidence: float
    threshold: float
    used_finetune: bool
    model_name: str
    model_path: Optional[str] = None
    model_id: Optional[int] = None
    finetune_name: Optional[str] = None
    base_model: Optional[str] = None
    fallback_to_pretrained: bool = False
    warning: Optional[str] = None
    message: Optional[str] = None


class AvailableModelOut(BaseModel):
    """系统支持的预训练模型 (前端下拉用)"""
    name: str
    params: str
    framework: str
    task_type: str
    recommended: bool = False
    description: Optional[str] = None
    imagenet_top1: Optional[float] = None
    coco_mAP50: Optional[float] = None
    coco_mIoU: Optional[float] = None


class AvailableModelsResponse(BaseModel):
    """预训练模型列表响应"""
    models: List[AvailableModelOut]


__all__ = [
    "AutoAnnotateRequest",
    "AutoAnnotateResponse",
    "AutoLabelRequest",
    "AutoLabelResponse",
    "AvailableModelOut",
    "AvailableModelsResponse",
]
