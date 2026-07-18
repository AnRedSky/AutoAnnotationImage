"""
TrainingJob ORM: 训练任务历史记录
================================
与 ModelVersion 不同, TrainingJob 记录每一次训练任务的元数据 (含失败任务),
ModelVersion 只记录成功的版本。两表通过 id/model_version_id 关联。
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, JSON, ForeignKey, Float, Boolean
)
from sqlalchemy.orm import relationship
from app.database import Base


class TrainingJob(Base):
    """训练任务历史表"""
    __tablename__ = "training_jobs"

    id = Column(Integer, primary_key=True, index=True)
    celery_task_id = Column(String(64), unique=True, index=True, nullable=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)

    # 输入参数
    dataset_id = Column(Integer, ForeignKey("dataset.id"), nullable=False)
    base_model = Column(String(64), nullable=False)
    model_name = Column(String(64), nullable=False)
    epochs = Column(Integer, default=20)
    batch_size = Column(Integer, default=32)
    learning_rate = Column(Float, default=1e-4)

    # 状态
    state = Column(String(32), default="PENDING", index=True)
    # PENDING / PROGRESS / SUCCESS / FAILURE / REVOKED

    progress = Column(Float, default=0.0)  # 0-100
    message = Column(Text, nullable=True)
    error = Column(Text, nullable=True)

    # 启动 / 结束时间
    # started_at: 任务被 worker 真正接手并开始执行的时间 (PROGRESS 起就有, PENDING 为空)
    # finished_at: 任务进入终态 (SUCCESS/FAILURE/REVOKED) 的时间
    # created_at: 任务在 API 端入库的时间 (PENDING 阶段就有), 与 started_at 区别:
    #   - 再训练时, 提交瞬间写入 created_at, worker 接手后写入 started_at
    #   - 列表展示"创建日期"用本字段, "开始时间"用 started_at
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    duration_seconds = Column(Float, nullable=True)

    # 关联产出 (成功才填充)
    model_version_id = Column(Integer, ForeignKey("model_version.id"), nullable=True)

    # 训练过程曲线
    history = Column(JSON, nullable=True)

    # 训练过程日志 (SSE 推送过的每行, 用于详情页持久化展示)
    # 结构: list[str], 每个元素是一行日志, 时间戳前缀已带
    # 限制: 最多保留 LOG_MAX_LINES 行, 避免 JSON 字段过大
    LOG_MAX_LINES = 200
    log = Column(JSON, nullable=True)

    # ---- 训练资源记录 (设备硬件) ----
    # 启动训练时由 train.py collect_device_info() 采集, tasks.py 写入
    # - device_type: "cuda" / "mps" / "cpu" (用于快速过滤)
    # - device_name: GPU 名称 / CPU 处理器 (用于显示)
    # - device_info: 完整 JSON, 含 cuda_version / cudnn / 显存 / CPU 核数 / RAM / torch / python
    device_type = Column(String(16), nullable=True)
    device_name = Column(String(128), nullable=True)
    device_info = Column(JSON, nullable=True)
    # GPU 峰值显存 (MB) - 仅 CUDA 训练时有意义
    gpu_peak_memory_mb = Column(Integer, nullable=True)

    # ---- 数据集统计 (训练启动那一刻由 train.py 推送, 持久化到 DB 供详情页展示) ----
    # 之前: 这些字段仅存在 Celery Redis state, 训练完成后 result.info 变成 return dict,
    #       详情页 /api/training/jobs/{id} 直接返回 ORM 行, 拿不到, 显示全 0
    # 现在: 训练启动那一刻同步入库, 详情/历史/重查均可恢复
    data_total = Column(Integer, nullable=True)   # 总样本数 (train+val)
    data_train = Column(Integer, nullable=True)   # 训练集大小
    data_val = Column(Integer, nullable=True)     # 验证集大小
    num_classes = Column(Integer, nullable=True)  # 类别数
    # 类别名列表 (排序后的 label_name_to_idx.keys())
    class_names = Column(JSON, nullable=True)

    # 关系
    user = relationship("User")
    dataset = relationship("Dataset")
    model_version = relationship("ModelVersion", foreign_keys=[model_version_id])
