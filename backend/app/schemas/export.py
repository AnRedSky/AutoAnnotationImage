"""Pydantic Schemas: Export (v3.0.0 Phase 4 新增)

数据导出 / 模型下载相关 schema.
"""
from typing import Optional
from pydantic import BaseModel, Field


class ExportRequest(BaseModel):
    """数据导出请求"""
    dataset_id: int
    format: str = Field(default="yolo", description="yolo / coco / voc / csv")
    include_images: bool = True
    include_ai_predictions: bool = False
    split_ratio: Optional[dict] = Field(
        default=None,
        description="数据集划分比例 {train: 0.8, val: 0.1, test: 0.1}",
    )


class ExportResponse(BaseModel):
    """数据导出响应 (异步任务)"""
    export_id: str
    dataset_id: int
    status: str = "pending"  # pending / running / done / failed
    download_url: Optional[str] = None
    file_size: Optional[int] = None
    message: Optional[str] = None


class ModelDownloadResponse(BaseModel):
    """模型下载响应"""
    model_id: int
    download_url: str
    file_size: Optional[int] = None
    expires_at: Optional[str] = None


__all__ = [
    "ExportRequest",
    "ExportResponse",
    "ModelDownloadResponse",
]
