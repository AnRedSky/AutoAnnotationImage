"""
Celery Configuration
====================
异步任务队列, 用于模型训练、批量推理
"""
from celery import Celery
from app.config import settings


celery_app = Celery(
    "image_annotation",
    broker=settings.CELERY_BROKER,
    backend=settings.CELERY_BACKEND,
    # include 让 worker 启动时自动 import 任务模块，
    # 这样 @celery_app.task 装饰器就会运行并把任务注册到 celery_app.tasks
    include=[
        "app.workers.tasks",
        "app.workers.detection_tasks",  # v2.0.0 目标检测
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_time_limit=3600,        # 1 hour hard limit
    task_soft_time_limit=3300,    # 55 min soft limit
    worker_max_tasks_per_child=10,
    worker_prefetch_multiplier=1,
    # 快速失败: Redis 不可用时不要长时间阻塞 .delay() 调用
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=2,
    broker_transport_options={"visibility_timeout": 30},
    result_backend_transport_options={"visibility_timeout": 30},
)
