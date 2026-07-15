"""Pydantic Schemas: Dataset"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class CategoryOut(BaseModel):
    id: int
    name: str
    sample_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)


class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None


class DatasetBase(BaseModel):
    name: str
    description: Optional[str] = None
    task_type: str = "classification"


class DatasetCreate(DatasetBase):
    category_names: List[str] = []


class DatasetOut(DatasetBase):
    id: int
    image_count: int = 0
    labeled_count: int = 0
    categories: List[CategoryOut] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
