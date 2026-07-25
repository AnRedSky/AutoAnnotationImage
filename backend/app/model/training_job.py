"""
兼容垫片 (Stage 2.3): TrainingJob ORM Model
=========================================

**v3.0.0 Stage 2.3 迁移**: TrainingJob 已迁入 app.tasks.model.training_job
"""
from app.tasks.model.training_job import *  # noqa: F401,F403
from app.tasks.model.training_job import (
    TrainingJob,
    TRAIN_STATE_VALUES,
    TRAIN_TERMINAL_STATES,
)  # noqa: F401


__all__ = ["TrainingJob", "TRAIN_STATE_VALUES", "TRAIN_TERMINAL_STATES"]
