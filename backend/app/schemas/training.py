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
    # 预创建 TrainingJob 行的 id; 前端提交后 GET /jobs 立即能看到
    # (之前是 worker 启动才写, 存在竞态窗口, 见 api/training.py:104-148)
    job_id: int | None = None
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
    # v2.5.28: 透传时间字段, 前端 REST 轮询也能拿到 started_at / finished_at
    # 与 SSE payload 保持一致, 避免两套接口行为分歧
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


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
    # 任务入库时间 (PENDING 阶段就有), 与 started_at 区分
    # - created_at: 任务在 API 端被提交入库的时间, 用来展示"创建日期"
    # - started_at: worker 真正开始训练的时间, PENDING 为空, 用来展示"开始日期"
    # - finished_at: 任务进入终态的时间, 用来展示"结束日期"
    created_at: Optional[datetime] = None
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

    # ---- 任务类型 (v2.0.0: classification / detection / segmentation) ----
    # 详情页 Training.vue S12.4 训练可视化按 task_type 切换曲线
    # (classification: loss/acc / detection: mAP/P/R / segmentation: mIoU/dice)
    # 模型 default="classification" + nullable=False, 这里给 schema 同样默认值
    task_type: Optional[str] = "classification"

    # ---- 数据集统计 (训练启动那一刻由 train.py 推送, 持久化到 DB) ----
    # 详情页 Training.vue 用这 4 个字段展示「数据集统计」4 联卡 (总样本/训练/验证/类数)
    # 之前: 训练完成后 (result.info = return dict) 这 4 个字段全 0, 用户看到的
    #       截图里「总样本数 0 张 / 训练集 0 张 / 验证集 0 张 / 类别数 0 类」
    # 现在: 持久化到 TrainingJob 表, 详情接口直接返回
    data_total: Optional[int] = None
    data_train: Optional[int] = None
    data_val: Optional[int] = None
    num_classes: Optional[int] = None
    class_names: Optional[List[str]] = None


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
    # mode=restart 时: 新预创建 TrainingJob 行的 id, 前端可以据此塞占位
    # mode=resume 时: 与 job_id 相同 (复用旧行)
    # mode=其他 (pause/cancel/delete): None
    new_job_id: Optional[int] = None


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
