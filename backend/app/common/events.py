"""
事件总线 (Common Layer)
========================

跨应用/插件通信总线, 避免 app 之间直接 import 造成的强耦合.

**核心组件**:
- `DomainEvent`: 领域事件数据类 (name, payload, occurred_at)
- `EventBus`: 事件总线单例 (publish / subscribe)
- `EventPriority`: 处理器优先级 (HIGH / NORMAL / LOW)

**使用示例**:
```python
# 在 app/admin/events.py
from app.common.events import EventBus, DomainEvent

@EventBus.subscribe("user.created", priority="normal")
async def on_user_created(payload: dict):
    user_id = payload["user_id"]
    # 初始化新用户的默认数据集
    await create_default_dataset(user_id)

# 在 app/admin/service/user_service.py
await EventBus.publish(DomainEvent(
    name="user.created",
    payload={"user_id": new_user.id},
))
```

**设计原则**:
- 事件订阅启动时注册 (在 AppInterface.startup 中)
- 事件处理失败不影响主流程 (异常隔离, log 记录)
- 同步 publish + 异步 handler (handler 用 asyncio.create_task 启动)
- 跨应用通信 100% 走 EventBus, 禁止直接 import

v3.0.0 Stage 2 新增
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventPriority(str, Enum):
    """事件处理器优先级"""
    HIGH = "high"      # 同步等待执行 (e.g. 数据一致性)
    NORMAL = "normal"  # 默认, 异步后台执行
    LOW = "low"        # 异步, 可延迟 (e.g. 通知/审计)


@dataclass
class DomainEvent:
    """领域事件"""
    name: str                                       # 事件名 (e.g. "user.created" / "task.started")
    payload: Dict[str, Any] = field(default_factory=dict)  # 事件数据
    occurred_at: datetime = field(default_factory=datetime.utcnow)  # 发生时间
    source: Optional[str] = None                    # 事件源 (app/插件名)

    def __post_init__(self):
        if not self.name:
            raise ValueError("DomainEvent.name must not be empty")


class EventBus:
    """事件总线单例 (跨应用通信)

    特性:
    - publish 同步触发, handler 异步执行
    - handler 异常隔离 (单 handler 失败不影响其他)
    - HIGH 优先级 handler 同步等待, NORMAL/LOW 异步后台
    - 支持通配符订阅 (e.g. "task.*" 订阅所有 task 事件)
    """

    _handlers: Dict[str, List[Dict[str, Any]]] = {}  # event_name -> [{handler, priority, name}]

    @classmethod
    def subscribe(
        cls,
        event_name: str,
        handler: Callable,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        name: Optional[str] = None,
    ) -> Callable:
        """订阅事件

        Args:
            event_name: 事件名 (支持通配符 "*" 后缀, e.g. "task.*")
            handler: 处理函数 (async 或 sync, 接受 payload: dict)
            priority: 优先级 (HIGH 同步, NORMAL/LOW 异步)
            name: 处理器名 (用于调试)

        Returns:
            原 handler 函数 (支持装饰器风格)
        """
        handler_info = {
            "handler": handler,
            "priority": priority,
            "name": name or handler.__name__ if hasattr(handler, "__name__") else str(handler),
        }
        cls._handlers.setdefault(event_name, []).append(handler_info)
        logger.debug(f"EventBus: subscribed {handler_info['name']} to '{event_name}' ({priority.value})")
        return handler

    @classmethod
    def unsubscribe(cls, event_name: str, handler: Callable) -> bool:
        """取消订阅"""
        if event_name not in cls._handlers:
            return False
        before = len(cls._handlers[event_name])
        cls._handlers[event_name] = [
            h for h in cls._handlers[event_name] if h["handler"] is not handler
        ]
        removed = before - len(cls._handlers[event_name])
        return removed > 0

    @classmethod
    async def publish(cls, event: DomainEvent) -> None:
        """发布事件 (触发订阅者)

        匹配逻辑:
        1. 精确匹配: handlers[event.name]
        2. 通配符匹配: handlers["*"] + handlers[event.name.split('.')[0] + '.*']

        执行顺序:
        - HIGH 优先级: 同步 await
        - NORMAL/LOW 优先级: asyncio.create_task 后台执行
        """
        # 收集匹配的 handlers
        matched = []
        # 1) 精确匹配
        matched.extend(cls._handlers.get(event.name, []))
        # 2) 通配符 (e.g. "task.*")
        if "." in event.name:
            prefix = event.name.split(".")[0] + ".*"
            matched.extend(cls._handlers.get(prefix, []))
        # 3) 全局通配符
        matched.extend(cls._handlers.get("*", []))

        if not matched:
            logger.debug(f"EventBus: no handlers for '{event.name}'")
            return

        # 按优先级排序 (HIGH first)
        priority_order = {EventPriority.HIGH: 0, EventPriority.NORMAL: 1, EventPriority.LOW: 2}
        matched.sort(key=lambda h: priority_order.get(h["priority"], 1))

        # 触发 handlers
        for h in matched:
            try:
                if h["priority"] == EventPriority.HIGH:
                    # 同步等待
                    await cls._invoke(h, event)
                else:
                    # 异步后台执行
                    asyncio.create_task(cls._invoke(h, event))
            except Exception as e:
                logger.exception(f"EventBus: handler {h['name']} raised {e!r}, event='{event.name}'")

    @classmethod
    async def _invoke(cls, handler_info: Dict[str, Any], event: DomainEvent) -> None:
        """调用 handler (异常隔离)"""
        handler = handler_info["handler"]
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event.payload)
            else:
                handler(event.payload)
        except Exception as e:
            logger.exception(
                f"EventBus handler '{handler_info['name']}' failed on event '{event.name}': {e!r}"
            )

    @classmethod
    def clear(cls) -> None:
        """清空所有订阅 (主要用于测试)"""
        cls._handlers.clear()

    @classmethod
    def list_subscribers(cls, event_name: Optional[str] = None) -> Dict[str, int]:
        """列出订阅情况 (调试用)"""
        if event_name:
            return {event_name: len(cls._handlers.get(event_name, []))}
        return {name: len(handlers) for name, handlers in cls._handlers.items()}


# ============== 预定义事件名常量 ==============

class EventNames:
    """预定义事件名 (避免硬编码字符串, 提供类型安全)"""

    # 用户相关
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"

    # 数据集相关
    DATASET_CREATED = "dataset.created"
    DATASET_DELETED = "dataset.deleted"

    # 图像相关
    IMAGE_UPLOADED = "image.uploaded"
    IMAGE_ANNOTATED = "image.annotated"
    IMAGE_DELETED = "image.deleted"

    # 任务相关
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_PAUSED = "task.paused"

    # 模型相关
    MODEL_TRAINED = "model.trained"
    MODEL_ACTIVATED = "model.activated"
    MODEL_DELETED = "model.deleted"

    # 标注相关
    ANNOTATION_CONFIRMED = "annotation.confirmed"
    ANNOTATION_CORRECTED = "annotation.corrected"
    AUTO_ANNOTATION_DONE = "auto_annotation.done"


__all__ = [
    "DomainEvent",
    "EventBus",
    "EventPriority",
    "EventNames",
]
