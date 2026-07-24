"""Pydantic Schemas: Stats (v3.0.0 Phase 4 新增)

统计接口的请求/响应 schema.
"""
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, ConfigDict, Field


class GlobalOverview(BaseModel):
    """全局概览 (首页 dashboard 用)"""
    total_datasets: int = 0
    total_images: int = 0
    total_annotated_images: int = 0
    total_training_jobs: int = 0
    active_training_jobs: int = 0
    total_users: int = 0
    total_models: int = 0
    # 按 task_type 拆分
    datasets_by_task_type: Dict[str, int] = Field(default_factory=dict)
    images_by_task_type: Dict[str, int] = Field(default_factory=dict)


class DatasetOverview(BaseModel):
    """单数据集概览"""
    dataset_id: int
    name: str
    task_type: str
    image_count: int = 0
    annotated_count: int = 0
    pending_count: int = 0
    category_count: int = 0
    ai_labeled_count: int = 0
    human_labeled_count: int = 0
    # 训练相关
    training_jobs_count: int = 0
    active_model_id: Optional[int] = None
    active_model_name: Optional[str] = None
    best_metric: Optional[Dict[str, Any]] = None


class CategoryStat(BaseModel):
    """单类目统计 (供类别管理弹窗)"""
    id: int
    name: str
    color: str
    sample_count: int = 0
    human_labeled_count: int = 0
    ai_labeled_count: int = 0
    ai_candidate_count: int = 0


class CategoryStatListOut(BaseModel):
    """类目统计列表响应"""
    items: List[CategoryStat]


__all__ = [
    "GlobalOverview",
    "DatasetOverview",
    "CategoryStat",
    "CategoryStatListOut",
]
