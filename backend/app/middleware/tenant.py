"""
Tenant Context & Middleware (v3.2.0 MT-4)
==========================================

请求级 tenant_id 上下文, 从 JWT 解析注入, 供 SQLAlchemy
do_orm_execute event listener 自动过滤.

设计:
  1. TenantContext: contextvar 存当前请求的 tenant_id
  2. TenantMiddleware: 从 JWT payload 解析 tenant_id, 注入 contextvar
  3. (后续) SQLAlchemy do_orm_execute: 自动给有 tenant_id 的 ORM entity 加 WHERE

super_admin (tenant_id is None) 跳过过滤 — 看全部数据.
"""
from __future__ import annotations

import contextvars
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class TenantContext:
    """请求级 tenant 上下文 (contextvar)."""
    _current: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
        "tenant_id", default=None,
    )

    @classmethod
    def set(cls, tenant_id: Optional[int]) -> None:
        cls._current.set(tenant_id)

    @classmethod
    def get(cls) -> Optional[int]:
        return cls._current.get()

    @classmethod
    def clear(cls) -> None:
        cls._current.set(None)


class TenantMiddleware(BaseHTTPMiddleware):
    """从 JWT token 解析 tenant_id, 注入 TenantContext.

    流程:
      1. 提取 Authorization header
      2. decode JWT (best-effort, 失败则跳过)
      3. 从 payload 取 tenant_id
      4. TenantContext.set(tenant_id)
      5. 请求结束后 TenantContext.clear()
    """

    async def dispatch(self, request: Request, call_next):
        # 跳过不需要鉴权的路径
        path = request.url.path
        if path.startswith(("/docs", "/redoc", "/openapi", "/api/health")):
            return await call_next(request)

        # 从 Authorization header 提取 token
        auth = request.headers.get("Authorization", "")
        token = None
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()

        if token:
            try:
                # best-effort decode, 不抛异常 (让 get_current_user 做严格校验)
                from app.middleware.security.security import decode_token_safe
                payload = decode_token_safe(token)
                if payload and isinstance(payload, dict):
                    tenant_id = payload.get("tenant_id")
                    if tenant_id is not None:
                        TenantContext.set(int(tenant_id))
            except Exception:
                pass  # 无效 token 让后续 auth 中间件处理

        try:
            response = await call_next(request)
        finally:
            TenantContext.clear()

        return response
