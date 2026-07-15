"""Pydantic Schemas: Image / Annotation"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, ConfigDict


class ImageOut(BaseModel):
    id: int
    dataset_id: int
    filename: str
    status: str
    width: Optional[int] = None
    height: Optional[int] = None
    file_size: Optional[int] = None
    ai_prediction: Optional[Dict[str, Any]] = None
    final_label_id: Optional[int] = None
    final_label_name: Optional[str] = None
    annotated_by: Optional[int] = None
    annotated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ImageListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ImageOut]


class AnnotationSaveRequest(BaseModel):
    image_id: int
    label_id: int
    time_spent_ms: int = 0
    is_confirm: bool = False


class AnnotationSaveResponse(BaseModel):
    success: bool
    image_id: int
    new_status: str
    label: str


class AnnotationLogOut(BaseModel):
    id: int
    image_id: int
    user_id: int
    action: str
    from_label_id: Optional[int] = None
    to_label_id: Optional[int] = None
    time_spent_ms: int
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
