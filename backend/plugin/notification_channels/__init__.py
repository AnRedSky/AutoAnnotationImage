"""
Notification Channels Subpackage
================================

通知渠道后端插件. 提供统一的实时通知/告警能力.

**Stage 4 新增 (框架 + 第一实现)**. 包含:
- `sse` — Server-Sent Events (前端实时推送, 默认, 已实现)
- (未来) `email` — 邮件通知
- (未来) `webhook` — Webhook 回调
- (未来) `websocket` — WebSocket 双向通信

**当前状态**:
- sse 已是可用包装, 通过 Redis pubsub 解耦
- 业务调用 `push(user_id, event, payload)` 写入 `notification:user:{user_id}` 频道
- 现有 SSE 端点订阅该频道后实时推给浏览器
- 其他渠道仅文档占位, Stage 5+ 逐步迁移

**使用方式**:
```python
from app.registry import PluginRegistry
notif = PluginRegistry.get_default("notification")
await notif.push(user_id=42, event="task.completed", payload={"task_id": "abc"})
```

**自动发现**:
导入本包会触发 `sse` 模块的 PluginRegistry.register() 调用.
"""
# 显式 import, 触发插件自动注册
from plugin.notification_channels.sse import SSENotificationPlugin  # noqa: E402, F401

__all__ = ["SSENotificationPlugin"]
