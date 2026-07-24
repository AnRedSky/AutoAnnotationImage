"""Pydantic Schemas: Annotation (v3.0.0 Phase 4 新增)

统一标注操作的 schema (跨 classification / detection / segmentation 任务).
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class AnnotationActionRequest(BaseModel):
    """统一标注动作请求 (3 任务共用入口)"""
    image_id: int
    action: str = Field(..., description="confirm / correct / reject")
    user_id: Optional[int] = None
    time_spent_ms: int = 0
    # 分类任务用
    label_id: Optional[int] = None
    # 检测任务用
    bboxes: Optional[List[Dict[str, Any]]] = None
    # 分割任务用
    mask_path: Optional[str] = None


class AnnotationActionResponse(BaseModel):
    """统一标注动作响应"""
    success: bool
    image_id: int
    new_status: str
    log_id: Optional[int] = None
    message: Optional[str] = None


class BBoxAnnotationIn(BaseModel):
    """BBox 标注入参 (供检测任务 API)"""
    x_min: float = Field(..., ge=0.0, le=1.0)
    y_min: float = Field(..., ge=0.0, le=1.0)
    x_max: float = Field(..., ge=0.0, le=1.0)
    y_max: float = Field(..., ge=0.0, le=1.0)
    category_id: Optional[int] = None
    confidence: Optional[float] = None
    source: Optional[str] = "human"


class SegmentationMaskIn(BaseModel):
    """Mask 入参 (供分割任务 API, base64 编码 PNG)"""
    image_id: int
    mask_base64: str = Field(..., description="base64 编码的 PNG 索引图")
    source: str = "human"
    overwrite_existing: bool = False
    crop_size: int = 256


__all__ = [
    "AnnotationActionRequest",
    "AnnotationActionResponse",
    "BBoxAnnotationIn",
    "SegmentationMaskIn",
]
