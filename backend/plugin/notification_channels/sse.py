"""
SSE Notification Channel Plugin
================================

Server-Sent Events 实时推送插件. Stage 4 第三个具体插件实现.

**职责**:
- 提供 `push(user_id, event, payload)` 统一通知入口
- 内部使用 Redis pubsub (与现有 SSE 实现一致), 不重新发明轮子
- 业务代码通过 `PluginRegistry.get_default("notification")` 获取

**依赖方向**:
- plugin/notification_channels/sse.py → app.database.redis (复用 Redis pubsub)
- plugin → 不直接依赖任何 app/* 业务代码

**当前实现**:
- 推送写入 Redis 频道 `notification:user:{user_id}`, 已有 SSE 端点订阅
- 提供 fallback: Redis 不可用时仅记录日志, 不抛异常 (不影响主流程)

**未来扩展**:
- EmailChannel: SMTP 邮件
- WebhookChannel: HTTP POST 回调
- WebSocketChannel: 双向通信
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional

from app.common.interfaces import PluginInterface
from app.registry import PluginRegistry

logger = logging.getLogger(__name__)

# Redis 频道前缀 (与现有 SSE 端点约定一致)
_CHANNEL_PREFIX = "notification:user:"


class SSENotificationPlugin(PluginInterface):
    """SSE 通知渠道插件 (默认)

    通过 Redis pubsub 解耦, 业务调用 push() 即写入频道,
    已有 SSE 端点 (前端 EventSource) 订阅频道后将数据实时推给浏览器.

    Attributes:
        name: "sse" (唯一)
        version: "1.0.0"
        category: "notification"
    """

    name = "sse"
    version = "1.0.0"
    category = "notification"

    def install(self) -> None:
        """插件安装: 验证 Redis 可用"""
        try:
            from app.database.redis import redis_client  # noqa: PLC0415
            # 健康检查: ping 一下, 但不抛异常
            try:
                redis_client.ping()
                logger.info(f"SSENotificationPlugin v{self.version}: install (Redis OK)")
            except Exception as e:
                logger.warning(
                    f"SSENotificationPlugin: Redis ping failed ({e!r}), "
                    f"通知降级为日志输出"
                )
        except ImportError:
            logger.warning("SSENotificationPlugin: redis_client not available, fallback to log")

    def uninstall(self) -> None:
        """插件卸载: 无需清理 (Redis 连接全局共享)"""
        logger.info("SSENotificationPlugin: uninstall (no-op)")

    # ============== 通知核心方法 ==============
    async def push(
        self,
        user_id: int,
        event: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        channel: Optional[str] = None,
    ) -> bool:
        """推送通知到指定用户的 SSE 频道

        Args:
            user_id: 目标用户 ID
            event: 事件名 (e.g. "task.completed" / "annotation.confirmed")
            payload: 事件数据 (dict, 可 JSON 序列化)
            channel: 自定义频道 (None = 自动用 notification:user:{user_id})

        Returns:
            True = 推送成功 (或降级为日志)
            False = 推送失败 (不影响主流程)
        """
        if channel is None:
            channel = f"{_CHANNEL_PREFIX}{user_id}"

        message = {
            "event": event,
            "payload": payload or {},
        }

        try:
            from app.database.redis import redis_client  # noqa: PLC0415
            # 同步 publish, 在独立线程执行避免阻塞 event loop
            await asyncio.to_thread(redis_client.publish, channel, json.dumps(message))
            logger.debug(f"SSENotificationPlugin: push {event} -> {channel}")
            return True
        except Exception as e:
            # Fallback: 降级为日志, 不抛异常
            logger.warning(
                f"SSENotificationPlugin: push failed ({e!r}), "
                f"event={event} user_id={user_id} payload={payload}"
            )
            return False

    def push_sync(
        self,
        user_id: int,
        event: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """同步版本 push (供同步上下文使用, e.g. Celery task)

        Args:
            user_id: 目标用户 ID
            event: 事件名
            payload: 事件数据

        Returns:
            True = 推送成功, False = 失败
        """
        channel = f"{_CHANNEL_PREFIX}{user_id}"
        message = json.dumps({
            "event": event,
            "payload": payload or {},
        })

        try:
            from app.database.redis import redis_client  # noqa: PLC0415
            redis_client.publish(channel, message)
            return True
        except Exception as e:
            logger.warning(f"SSENotificationPlugin: push_sync failed ({e!r})")
            return False


# ============== 自动注册 ==============
PluginRegistry.register(SSENotificationPlugin(), make_default=True)


__all__ = ["SSENotificationPlugin"]
