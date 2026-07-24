"""Pydantic Schemas: ModelVersion (v3.0.0 Phase 4 新增)

model_version 相关的请求/响应 schema, 集中管理避免散落 API.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class ModelVersionOut(BaseModel):
    """模型版本输出"""
    id: int
    dataset_id: int
    name: str
    task_type: str
    base_model: str
    file_path: str
    num_classes: Optional[int] = None
    metrics: Optional[Dict[str, Any]] = None
    is_active: bool = False
    created_at: Optional[datetime] = None
    # 业务字段 (前端展示用)
    mAP50: Optional[float] = None
    mAP50_95: Optional[float] = None
    accuracy: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class ModelVersionListOut(BaseModel):
    """模型版本列表 (按 dataset 过滤)"""
    total: int
    items: List[ModelVersionOut]


class ModelActivationRequest(BaseModel):
    """激活模型请求 (目前无需 body, 保留扩展)"""
    pass


class ModelActivationResponse(BaseModel):
    """激活响应 (含单激活不变量)"""
    activated_id: int
    deactivated_ids: List[int] = Field(default_factory=list)
    dataset_id: int


__all__ = [
    "ModelVersionOut",
    "ModelVersionListOut",
    "ModelActivationRequest",
    "ModelActivationResponse",
]
