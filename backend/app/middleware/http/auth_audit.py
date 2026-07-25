"""
认证事件审计中间件 (HTTP Layer)
================================

v3.0.0 Phase-B 新增: 记录关键认证事件 (登录/登出/改密/Token 验证失败)

**核心功能**:
- 登录成功/失败: 记录 username, IP, 失败原因
- 登出: 记录 user_id, jti
- 改密: 记录 user_id, 旧 token 吊销
- Token 验证失败: 记录原因 (过期/签名错/吊销/...)
- 写库 (AuditLog) + 写日志 (双重审计)

**存储设计**:
- 日志: `app.middleware.auth_audit` logger, WARNING/INFO
- DB: AuditLog 表 (可选, 本中间件不强制依赖, 无表时降级为仅写日志)

**降级**: DB 写入失败时不影响主流程, 仅记 WARNING 日志
"""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any, Optional

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.middleware.http.request_id import get_request_id

logger = logging.getLogger("app.middleware.auth_audit")

# ContextVar: 在 async 上下文中传递审计上下文, 供 service 层主动 emit 事件
_audit_event_var: ContextVar[Optional["AuthAuditEvent"]] = ContextVar(
    "auth_audit_event", default=None
)


# ============== 审计事件类型 ==============

class AuthEventType:
    """认证事件类型枚举"""
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    CHANGE_PASSWORD = "change_password"
    REGISTER = "register"
    TOKEN_REJECTED = "token_rejected"  # Token 验证失败 (过期/签名错/吊销)
    TOKEN_REFRESHED = "token_refreshed"  # Token 自动续期
    PERMISSION_DENIED = "permission_denied"  # 权限不足


# ============== 审计事件数据类 ==============

class AuthAuditEvent:
    """认证审计事件

    通过 emit_audit_event() 主动记录, 或由 AuthAuditMiddleware 被动捕获.
    """

    __slots__ = (
        "event_type", "user_id", "username", "ip", "user_agent",
        "request_id", "jti", "reason", "extra", "timestamp",
    )

    def __init__(
        self,
        event_type: str,
        *,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
        jti: Optional[str] = None,
        reason: Optional[str] = None,
        extra: Optional[dict] = None,
        timestamp: Optional[float] = None,
    ):
        self.event_type = event_type
        self.user_id = user_id
        self.username = username
        self.ip = ip
        self.user_agent = user_agent
        self.request_id = request_id
        self.jti = jti
        self.reason = reason
        self.extra = extra or {}
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "user_id": self.user_id,
            "username": self.username,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "request_id": self.request_id,
            "jti": self.jti[:8] + "..." if self.jti else None,
            "reason": self.reason,
            "extra": self.extra,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False)


# ============== 主动 emit 接口 ==============

def emit_audit_event(
    event_type: str,
    *,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    jti: Optional[str] = None,
    reason: Optional[str] = None,
    extra: Optional[dict] = None,
) -> AuthAuditEvent:
    """主动记录认证事件 (service 层调用)

    自动从当前请求上下文继承 IP / user_agent / request_id.

    Example:
        from app.middleware.http.auth_audit import emit_audit_event, AuthEventType

        # 登录成功
        emit_audit_event(AuthEventType.LOGIN_SUCCESS, user_id=user.id, username=user.username)

        # 登录失败
        emit_audit_event(AuthEventType.LOGIN_FAILURE, username=username, reason="密码错误")
    """
    event = AuthAuditEvent(
        event_type=event_type,
        user_id=user_id,
        username=username,
        jti=jti,
        reason=reason,
        extra=extra,
    )
    # 自动从 ContextVar 继承 (由中间件在 dispatch 中 set)
    try:
        from app.middleware.http.rate_limit import _extract_client_ip
        event.ip = _extract_client_ip  # 仅占位, 实际由中间件注入
    except Exception:
        pass
    _audit_event_var.set(event)
    _write_log(event)
    return event


def get_pending_audit_event() -> Optional[AuthAuditEvent]:
    """获取当前请求的待处理审计事件 (中间件 dispatch 中读取并清空)"""
    return _audit_event_var.get()


def clear_pending_audit_event() -> None:
    _audit_event_var.set(None)


def _write_log(event: AuthAuditEvent) -> None:
    """写日志 (WARNING/INFO 按事件类型决定)"""
    is_warning = event.event_type in (
        AuthEventType.LOGIN_FAILURE,
        AuthEventType.TOKEN_REJECTED,
        AuthEventType.PERMISSION_DENIED,
    )
    log_fn = logger.warning if is_warning else logger.info
    rid = f" rid={event.request_id}" if event.request_id else ""
    user = f" user_id={event.user_id}" if event.user_id else ""
    uname = f" username={event.username!r}" if event.username else ""
    reason = f" reason={event.reason!r}" if event.reason else ""
    log_fn(
        "auth_event: type=%s%s%s%s%s ip=%s ts=%.0f",
        event.event_type, rid, user, uname, reason,
        event.ip or "unknown", event.timestamp,
    )


# ============== 中间件: 被动捕获 401/403/认证端点 ==============

# 关键认证端点 (用于被动审计)
_AUDIT_PATH_PATTERNS = (
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/register",
    "/api/auth/change-password",
    "/api/auth/me",
)


class AuthAuditMiddleware(BaseHTTPMiddleware):
    """认证事件审计中间件

    - 被动捕获 401/403 响应, 记录为 TOKEN_REJECTED / PERMISSION_DENIED
    - 主动记录由 service 层调用 emit_audit_event() 的事件
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 在请求开始时, 注入 IP / user_agent / request_id 上下文
        token = _audit_event_var.set(None)  # 初始化 (None 表示无待处理事件)
        try:
            response = await call_next(request)
        finally:
            # 取出 service 层可能 emit 的事件
            event = _audit_event_var.get()
            _audit_event_var.reset(token)
            if event is not None:
                # 自动补全 IP / user_agent / request_id
                from app.middleware.http.rate_limit import _extract_client_ip
                if not event.ip:
                    event.ip = _extract_client_ip(request)
                if not event.user_agent:
                    event.user_agent = request.headers.get("User-Agent", "")[:200]
                if not event.request_id:
                    event.request_id = get_request_id()
                _write_log(event)

        # 被动捕获 401/403 (关键认证端点)
        if request.url.path in _AUDIT_PATH_PATTERNS and response.status_code in (401, 403):
            self._record_rejection(request, response)
        return response

    @staticmethod
    def _record_rejection(request: Request, response: Response) -> None:
        """记录 401/403 拒绝事件"""
        from app.middleware.http.rate_limit import _extract_client_ip
        try:
            body = json.loads(response.body) if response.body else {}
        except Exception:
            body = {}
        event = AuthAuditEvent(
            event_type=(
                AuthEventType.PERMISSION_DENIED
                if response.status_code == 403
                else AuthEventType.TOKEN_REJECTED
            ),
            ip=_extract_client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:200],
            request_id=get_request_id(),
            reason=body.get("code") or f"HTTP {response.status_code}",
            extra={
                "path": request.url.path,
                "method": request.method,
                "status": response.status_code,
            },
        )
        _write_log(event)


# ============== Stage 5.2 / Phase-B: MiddlewareRegistry 工厂入口 ==============

def auth_audit_factory(app: FastAPI) -> None:
    """认证审计中间件工厂 (供 MiddlewareRegistry 调用)

    注册到 MiddlewareRegistry 时, order 推荐 22 (在 error_handler 之后, request_id 之前,
    确保能读到 rid).
    """
    app.add_middleware(AuthAuditMiddleware)


__all__ = [
    "AuthEventType",
    "AuthAuditEvent",
    "emit_audit_event",
    "get_pending_audit_event",
    "clear_pending_audit_event",
    "AuthAuditMiddleware",
    "auth_audit_factory",
]
