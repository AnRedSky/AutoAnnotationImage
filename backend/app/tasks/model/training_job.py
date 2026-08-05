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
# v3.5.0: 新增 CANCELED 状态, 区分"用户主动取消"与 Celery 自动撤销 (REVOKED)
# - REVOKED: Celery 自身撤销 (历史遗留, 多用于信号 kill_job 路径)
# - CANCELED: 用户在前端点"取消"按钮, 走业务级 Redis signal 路径
TRAIN_STATE_CANCELED = "CANCELED"
TRAIN_STATE_VALUES = (
    TRAIN_STATE_PENDING, TRAIN_STATE_PROGRESS, TRAIN_STATE_SUCCESS,
    TRAIN_STATE_FAILURE, TRAIN_STATE_REVOKED, TRAIN_STATE_PAUSED,
    TRAIN_STATE_CANCELED,
)
# 终态 (不可再转移); CANCELED 是终态, 用户点"再训练"会创建新 job
TRAIN_TERMINAL_STATES = frozenset((
    TRAIN_STATE_SUCCESS, TRAIN_STATE_FAILURE, TRAIN_STATE_REVOKED,
    TRAIN_STATE_CANCELED,
))

# 预训练模式 (v3.0.0 新增, 记录任务是基于哪个 MV 训练, 用于追溯)
# - from_scratch: 微调 (基于 timm ImageNet 预训练权重, 不依赖业务 MV; 沿用旧名便于 DB 兼容)
# - incremental:  增量训练 / 再训练 (基于某个已有 ModelVersion 继续)
# - resume:       继续训练 (继续暂停的同 job, 复用 model_name)
#
# 注意: DB 存的值仍是 from_scratch/incremental/resume (英文枚举, 跨语言稳定);
#       中文 label 在 PRETRAIN_MODE_LABELS 中维护, 业务侧"微调/增量/继续"按需调整.
PRETRAIN_MODE_FROM_SCRATCH = "from_scratch"
PRETRAIN_MODE_INCREMENTAL = "incremental"
PRETRAIN_MODE_RESUME = "resume"
PRETRAIN_MODE_VALUES = (PRETRAIN_MODE_FROM_SCRATCH, PRETRAIN_MODE_INCREMENTAL, PRETRAIN_MODE_RESUME)
# 前端展示的中文 label (UI 文案; 后端不依赖这些 label 做业务判断)
# - v3.0.0 改版: from_scratch 展示为「微调」, 业务侧不再用「从头训练」表述
PRETRAIN_MODE_LABELS = {
    PRETRAIN_MODE_FROM_SCRATCH: "微调",
    PRETRAIN_MODE_INCREMENTAL: "增量",
    PRETRAIN_MODE_RESUME: "继续训练",
}


class TrainingJob(Base):
    """训练任务历史表"""
    __tablename__ = "training_jobs"

    id = Column(Integer, primary_key=True, index=True)
    celery_task_id = Column(String(64), unique=True, index=True, nullable=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)

    # 输入参数
    dataset_id = Column(Integer, ForeignKey("dataset.id"), nullable=False)
    # v3.5.0 Phase T7 #4 优化: 加 index=True
    # 列表搜索 q 走 ilike('%xxx%') OR ilike('%xxx%') (LIKE 前缀通配符用不上 B-tree,
    # 但至少 = / 前缀搜索受益, 配合 Redis list 端点缓存 (dataset_id, state, task_type, q)
    # 整体将模糊搜索从全表扫降到 5-30s TTL 命中)
    # 训练任务表行数上 10w 后, 这两个索引是必备 (否则每次搜索都全表)
    base_model = Column(String(64), nullable=False, index=True)
    model_name = Column(String(128), nullable=False, index=True)
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
    # 预训练模式 (v3.0.0 新增): from_scratch / incremental / resume
    # NULL = 历史任务 (迁移前创建), 前端显示「未知」
    pretrain_mode = Column(String(16), nullable=True, index=True)
    # 来源 ModelVersion ID (仅 pretrain_mode=incremental 时有值)
    # 记录"这个训练是基于哪个 MV 继续的", 用于详情页追溯
    pretrain_source_mv_id = Column(Integer, ForeignKey("model_version.id"), nullable=True)
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

    def is_canceled(self) -> bool:
        """v3.5.0: 是否用户主动取消 (与 is_revoked 区分业务语义)."""
        return self.state == TRAIN_STATE_CANCELED

    def is_classification(self) -> bool:
        return self.task_type == "classification"

    def is_detection(self) -> bool:
        return self.task_type == "detection"

    def is_segmentation(self) -> bool:
        return self.task_type == "segmentation"

    def transition_to(self, new_state: str, message: Optional[str] = None) -> None:
        """业务规则: 状态机转移 (非法转移抛异常)

        PENDING -> PROGRESS -> SUCCESS / FAILURE / REVOKED / CANCELED
        PAUSED  -> PROGRESS (继续) / REVOKED / CANCELED
        FAILURE/REVOKED/CANCELED 不可再转移 (终态)
        """
        _transitions = {
            TRAIN_STATE_PENDING: (TRAIN_STATE_PROGRESS, TRAIN_STATE_REVOKED, TRAIN_STATE_FAILURE, TRAIN_STATE_CANCELED),
            TRAIN_STATE_PROGRESS: (TRAIN_STATE_SUCCESS, TRAIN_STATE_FAILURE, TRAIN_STATE_REVOKED, TRAIN_STATE_PAUSED, TRAIN_STATE_CANCELED),
            TRAIN_STATE_SUCCESS: (),  # 终态
            TRAIN_STATE_FAILURE: (),  # 终态
            TRAIN_STATE_REVOKED: (),  # 终态
            TRAIN_STATE_CANCELED: (),  # 终态 (v3.5.0 新增)
            TRAIN_STATE_PAUSED: (TRAIN_STATE_PROGRESS, TRAIN_STATE_REVOKED, TRAIN_STATE_CANCELED),
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
