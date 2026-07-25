"""
TrainingJob ORM Model (Active Record) — app/tasks/model/
======================================================

**v3.0.0 Stage 2.3 迁移**: 从 app/model/training_job.py 迁入 tasks 应用
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, JSON, ForeignKey, Float, Boolean
)
from sqlalchemy.orm import relationship
from app.common.base_model import Base


# 训练状态枚举 (与 Celery 状态约定对齐)
TRAIN_STATE_PENDING = "PENDING"
TRAIN_STATE_PROGRESS = "PROGRESS"
TRAIN_STATE_SUCCESS = "SUCCESS"
TRAIN_STATE_FAILURE = "FAILURE"
TRAIN_STATE_REVOKED = "REVOKED"
TRAIN_STATE_PAUSED = "PAUSED"
TRAIN_STATE_VALUES = (
    TRAIN_STATE_PENDING, TRAIN_STATE_PROGRESS, TRAIN_STATE_SUCCESS,
    TRAIN_STATE_FAILURE, TRAIN_STATE_REVOKED, TRAIN_STATE_PAUSED,
)
# 终态 (不可再转移)
TRAIN_TERMINAL_STATES = frozenset((TRAIN_STATE_SUCCESS, TRAIN_STATE_FAILURE, TRAIN_STATE_REVOKED))


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
    task_type = Column(
        String(32), default="classification", nullable=False, index=True,
    )
    epochs = Column(Integer, default=20)
    batch_size = Column(Integer, default=32)
    learning_rate = Column(Float, default=1e-4)

    # 状态
    state = Column(String(32), default="PENDING", index=True)
    progress = Column(Float, default=0.0)  # 0-100
    message = Column(Text, nullable=True)
    error = Column(Text, nullable=True)

    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    duration_seconds = Column(Float, nullable=True)

    model_version_id = Column(Integer, ForeignKey("model_version.id"), nullable=True)
    history = Column(JSON, nullable=True)
    log = Column(JSON, nullable=True)

    LOG_MAX_LINES = 200

    device_type = Column(String(16), nullable=True)
    device_name = Column(String(128), nullable=True)
    device_info = Column(JSON, nullable=True)
    gpu_peak_memory_mb = Column(Integer, nullable=True)

    data_total = Column(Integer, nullable=True)
    data_train = Column(Integer, nullable=True)
    data_val = Column(Integer, nullable=True)
    num_classes = Column(Integer, nullable=True)
    class_names = Column(JSON, nullable=True)

    # 关系
    user = relationship("User")
    dataset = relationship("Dataset")
    model_version = relationship("ModelVersion", foreign_keys=[model_version_id])

    # ============== Active Record 业务方法 ==============

    def is_terminal(self) -> bool:
        """是否进入终态 (不可再转移)"""
        return self.state in TRAIN_TERMINAL_STATES

    def is_pending(self) -> bool:
        return self.state == TRAIN_STATE_PENDING

    def is_running(self) -> bool:
        return self.state == TRAIN_STATE_PROGRESS

    def is_succeeded(self) -> bool:
        return self.state == TRAIN_STATE_SUCCESS

    def is_failed(self) -> bool:
        return self.state == TRAIN_STATE_FAILURE

    def is_revoked(self) -> bool:
        return self.state == TRAIN_STATE_REVOKED

    def is_classification(self) -> bool:
        return self.task_type == "classification"

    def is_detection(self) -> bool:
        return self.task_type == "detection"

    def is_segmentation(self) -> bool:
        return self.task_type == "segmentation"

    def transition_to(self, new_state: str, message: Optional[str] = None) -> None:
        """业务规则: 状态机转移 (非法转移抛异常)

        PENDING -> PROGRESS -> SUCCESS / FAILURE / REVOKED
        FAILURE 不可再转移 (终态); REVOKED 不可再转移
        """
        _transitions = {
            TRAIN_STATE_PENDING: (TRAIN_STATE_PROGRESS, TRAIN_STATE_REVOKED, TRAIN_STATE_FAILURE),
            TRAIN_STATE_PROGRESS: (TRAIN_STATE_SUCCESS, TRAIN_STATE_FAILURE, TRAIN_STATE_REVOKED),
            TRAIN_STATE_SUCCESS: (),  # 终态
            TRAIN_STATE_FAILURE: (),  # 终态
            TRAIN_STATE_REVOKED: (),  # 终态
            TRAIN_STATE_PAUSED: (TRAIN_STATE_PROGRESS, TRAIN_STATE_REVOKED),
        }
        allowed = _transitions.get(self.state, ())
        if new_state not in allowed:
            raise ValueError(
                f"非法训练状态转移: {self.state!r} -> {new_state!r} "
                f"(job_id={self.id})"
            )
        self.state = new_state
        if message:
            self.message = message

    def update_progress(self, progress: float, message: Optional[str] = None) -> None:
        """更新进度 (0-100), 任意状态都允许"""
        self.progress = max(0.0, min(100.0, float(progress)))
        if message:
            self.message = message

    def mark_started(self) -> None:
        """标记开始 (PENDING -> PROGRESS)"""
        self.transition_to(TRAIN_STATE_PROGRESS)
        if not self.started_at:
            self.started_at = datetime.utcnow()

    def mark_succeeded(self, model_version_id: Optional[int] = None) -> None:
        """标记成功"""
        self.transition_to(TRAIN_STATE_SUCCESS, "训练完成")
        self.progress = 100.0
        self.finished_at = datetime.utcnow()
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()
        if model_version_id is not None:
            self.model_version_id = model_version_id

    def mark_failed(self, error: str) -> None:
        """标记失败 (任意非终态 -> FAILURE)"""
        if self.is_terminal():
            return  # 已是终态, 不再覆盖
        self.state = TRAIN_STATE_FAILURE
        self.error = error[:65535] if error else None
        self.finished_at = datetime.utcnow()
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()

    def append_log(self, line: str) -> None:
        """追加一行训练日志 (限制最大行数)"""
        if self.log is None:
            self.log = []
        if not isinstance(self.log, list):
            self.log = []
        # 限制最大行数
        if len(self.log) >= self.LOG_MAX_LINES:
            self.log = self.log[-(self.LOG_MAX_LINES - 1):]
        self.log.append(line)

    def get_class_names(self) -> List[str]:
        """类别名列表 (排序后)"""
        if not self.class_names:
            return []
        return list(self.class_names)

    def __repr__(self) -> str:
        return (
            f"<TrainingJob {self.id} {self.task_type} "
            f"{self.state} {self.progress:.0f}% model={self.model_name!r}>"
        )
