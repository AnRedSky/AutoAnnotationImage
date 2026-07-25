"""
兼容垫片 (Stage 2.6): celery_app
================================

**v3.0.0 Stage 2.6 迁移**: Celery 实例已迁入 app.tasks.workers.celery_app,
本文件 re-export 同一对象, 避免任务重复注册 (Celery 任务注册是模块级的,
新旧路径都引用同一个 celery_app, 注册只发生一次).
"""
from app.tasks.workers.celery_app import celery_app  # noqa: F401
from app.tasks.workers.celery_app import celery_app as _celery_app  # noqa: F401


__all__ = ["celery_app", "_celery_app"]
