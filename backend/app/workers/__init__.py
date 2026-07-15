"""Workers Package

导入 tasks 模块以确保 @celery_app.task 装饰器在包被 import 时就被执行，
任务会注册到 celery_app.tasks。这是 celery_app.py 的 include=[...] 参数之外的
第二道保险：任何 import `app.workers` 或 `app.workers.celery_app` 的地方都会
顺带把 tasks 也带进来。
"""
from app.workers import tasks  # noqa: F401,E402  (register celery tasks)
