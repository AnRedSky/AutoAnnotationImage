"""
兼容垫片 (Stage 2.6): workers __init__
=======================================

**v3.0.0 Stage 2.6 迁移**: 原 app.workers 内容已迁入 app.tasks.workers,
本文件 re-export 让旧 import 路径 (例如 `from app.workers import tasks`) 继续可用.
"""
# 导入子模块, 触发 @celery_app.task 装饰器, 把任务注册到 celery_app.tasks
from app.tasks.workers import (  # noqa: F401
    classification,
    detection,
    segmentation,
)


__all__ = ["classification", "detection", "segmentation"]
