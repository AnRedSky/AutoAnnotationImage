"""Pydantic Schemas: Training & ModelVersion"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


class TrainStartRequest(_Base):
    dataset_id: int
    base_model: str = "efficientnet_b0"
    model_name: str = "v1"
    epochs: int = 20
    batch_size: int = 32
    learning_rate: float = 1e-4


class TrainStartResponse(_Base):
    task_id: str
    celery_task_id: str
    state: str = "PENDING"
    message: str = "Training task submitted"


class TrainStatusResponse(_Base):
    task_id: str
    state: str
    progress: float = 0.0
    current_epoch: Optional[int] = None
    total_epochs: Optional[int] = None
    message: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None


class ModelVersionOut(_Base):
    id: int
    name: str
    base_model: str
    dataset_id: int
    num_classes: int
    file_path: str
    accuracy: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None
    training_log: Optional[Dict[str, Any]] = None
    confusion_matrix: Optional[Any] = None
    is_active: bool = False
    created_at: Optional[datetime] = None


class TrainingJobOut(_Base):
    id: int
    celery_task_id: Optional[str] = None
    user_id: int
    dataset_id: int
    base_model: str
    model_name: str
    epochs: int
    batch_size: int
    learning_rate: float
    state: str
    progress: float = 0.0
    message: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    model_version_id: Optional[int] = None
    history: Optional[List[Dict[str, Any]]] = None
    # ---- 训练资源 (device_type / device_name / device_info / gpu_peak_memory_mb) ----
    # device_info: 完整 JSON, 含 cuda_version / cudnn / 显存 / CPU 核数 / RAM / torch / python
    device_type: Optional[str] = None
    device_name: Optional[str] = None
    device_info: Optional[Dict[str, Any]] = None
    gpu_peak_memory_mb: Optional[int] = None


class TrainingJobList(_Base):
    """分页列表响应 - 前端 el-pagination 直接对接"""
    total: int
    items: List[TrainingJobOut]
    page: int
    page_size: int


class TrainingJobActionResult(_Base):
    """统一动作结果 (启动/暂停/取消/删除/复用启动)"""
    success: bool
    job_id: int
    state: Optional[str] = None
    message: Optional[str] = None
    task_id: Optional[str] = None  # 仅 start 复用时有值


class TrainingJobUpdate(_Base):
    """更新训练任务参数 - 仅允许未运行任务 (PENDING/PAUSED/终态)"""
    dataset_id: Optional[int] = None
    base_model: Optional[str] = None
    model_name: Optional[str] = None
    epochs: Optional[int] = None
    batch_size: Optional[int] = None
    learning_rate: Optional[float] = None


class TrainingJobLogAppend(_Base):
    """追加一行训练日志 (前端 SSE 收到推送时持久化到 DB)"""
    line: str


class TrainingJobLogOut(_Base):
    """返回完整训练日志 (用于详情页打开时拉取历史日志)"""
    log: List[str] = []
