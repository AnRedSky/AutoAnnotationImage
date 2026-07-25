"""
Task Queues Subpackage
======================

异步任务队列后端插件. 提供统一的任务提交/查询/取消能力.

**Stage 4 新增 (框架 + 第一实现)**. 包含:
- `celery` — Celery 任务队列 (默认, 已实现)
- (未来) `rq` — Redis Queue
- (未来) `dramatiq` — Dramatiq

**当前状态**:
- celery 已是可用包装, 委托给 `app.tasks.workers.celery_app.celery_app`
- 其他队列仅文档占位, Stage 5+ 逐步迁移

**使用方式**:
```python
from app.registry import PluginRegistry
queue = PluginRegistry.get_default("task_queue")
task_id = queue.enqueue("app.tasks.workers.classification.run_training", dataset_id=1)
state = queue.get_state(task_id)
```

**自动发现**:
导入本包会触发 `celery` 模块的 PluginRegistry.register() 调用.
"""
# 显式 import, 触发插件自动注册
from plugin.task_queues.celery import CeleryTaskQueuePlugin  # noqa: E402, F401

__all__ = ["CeleryTaskQueuePlugin"]
