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
    # v3.1.0 Phase W2.1: visibility_timeout 30s → 3600s
    # 旧值 30s 会导致训练任务 (最长 1h) 被 worker 心跳延迟后重新投递, 浪费算力.
    # 调至 3600s 与 task_time_limit 对齐, 避免长任务重投.
    broker_transport_options={"visibility_timeout": 3600},
    result_backend_transport_options={"visibility_timeout": 3600},
    # ===== Worker 并发默认值 (CLI --pool/--concurrency 可覆盖) =====
    # solo 池下 worker_concurrency 被忽略, threads 池下表示同时跑的线程数
    # Windows 上 prefork 不可用, 推荐 threads
    worker_pool=settings.CELERY_WORKER_POOL,
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
    # v3.1.0 Phase W2.2: 训练任务路由到独立队列, 避免 CPU 密集训练与 I/O 标注互踩
    # train 队列: 训练任务 (CPU 密集, 建议 concurrency=1)
    # annotate 队列: 自动标注 (I/O 密集, 可 concurrency=2+)
    task_routes={
        "app.tasks.workers.classification.train_model_task": {"queue": "train"},
        "app.tasks.workers.detection.train_detection_task": {"queue": "train"},
        "app.tasks.workers.segmentation.train_segmentation_task": {"queue": "train"},
        "app.tasks.workers.classification.auto_annotate_task": {"queue": "annotate"},
        "app.tasks.workers.detection.auto_annotate_detection_task": {"queue": "annotate"},
        "app.tasks.workers.detection.auto_annotate_pretrained_task": {"queue": "annotate"},
        "app.tasks.workers.segmentation.auto_annotate_segmentation_task": {"queue": "annotate"},
    },
)
