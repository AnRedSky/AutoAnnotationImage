"""
Celery Configuration (v3.0.0 Stage 2.6 重定位)
================================================
异步任务队列, 用于模型训练、批量推理.

**v3.0.0 Stage 2.6 迁移**: 原 app.tasks.workers.celery_app 重定位至 app.tasks.workers.celery_app,
app.tasks.workers.celery_app 转为兼容垫片 (re-export 同一对象, 避免 task 重复注册).
"""
from celery import Celery
from app.core.config import settings


celery_app = Celery(
    "image_annotation",
    broker=settings.CELERY_BROKER,
    backend=settings.CELERY_BACKEND,
    # include 让 worker 启动时自动 import 任务模块，
    # 这样 @celery_app.task 装饰器就会运行并把任务注册到 celery_app.tasks
    # v3.0.0 Stage 2.6 迁移: include 路径全部从 app.tasks.workers.* 改为 app.tasks.workers.*
    include=[
        "app.tasks.workers.classification",  # 原 app.tasks.workers.classification
        "app.tasks.workers.detection",       # 原 app.tasks.workers.detection
        "app.tasks.workers.segmentation",    # 原 app.tasks.workers.segmentation
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
    # ===== Worker 并发默认值 (CLI --pool/--concurrency 可覆盖) =====
    # solo 池下 worker_concurrency 被忽略, threads 池下表示同时跑的线程数
    # Windows 上 prefork 不可用, 推荐 threads
    worker_pool=settings.CELERY_WORKER_POOL,
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
)
