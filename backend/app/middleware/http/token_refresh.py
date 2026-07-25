"""
Token 自动续期中间件 (HTTP Layer)
==================================

v3.0.0 Phase-B 新增: 在业务请求中隐含静默续期

**核心功能**:
- 检测当前请求的 JWT 是否临近过期 (< threshold, 默认 5min)
- 若临近过期, 主动签发新 token 并通过响应头返回
- 前端拦截器拿到新 token 后覆盖 localStorage
- 用户无感知, 避免 token 过期导致突然登出

**响应头约定**:
- `X-Renewed-Token: <new_jwt>` - 新 token
- `X-Renewed-Token-Expires-At: <unix_ts>` - 新 token 过期时间

**前端集成** (frontend/src/api/http.ts):
```typescript
http.interceptors.response.use((response) => {
  const renewed = response.headers['x-renewed-token']
  if (renewed) {
    localStorage.setItem('token', renewed)
  }
  return response.data
})
```

**降级**: 续期失败 (Redis 不可用) 时不阻塞业务, 旧 token 继续使用直到自然过期
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.middleware.security.security import (
    create_access_token,
    decode_token,
    TokenValidationError,
)

logger = logging.getLogger("app.middleware.token_refresh")

# ============== 配置 ==============

# 默认续期阈值: 剩余有效期 < 5min 时触发续期
DEFAULT_REFRESH_THRESHOLD_SECONDS = 300

# 不触发续期的路径 (认证端点自身, 避免重复签发)
_EXEMPT_PATHS = (
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/refresh",
    "/api/auth/register",
    "/api/auth/change-password",
    "/api/auth/me",
)

# 续期后响应头
_RENEWED_TOKEN_HEADER = "X-Renewed-Token"
_RENEWED_EXPIRES_HEADER = "X-Renewed-Token-Expires-At"


def _extract_token(request: Request) -> Optional[str]:
    """从请求中提取 token (优先 Authorization, 兜底 query)"""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 兜底: query token (SSE 用)
    return request.query_params.get("token")


class TokenRefreshMiddleware(BaseHTTPMiddleware):
    """Token 自动续期中间件

    行为:
        1. 拦截所有非 exempt 路径的请求
        2. 提取并校验 token
        3. 若剩余有效期 < threshold, 签发新 token
        4. 通过响应头返回新 token
    """

    def __init__(self, app, *, refresh_threshold_seconds: int = DEFAULT_REFRESH_THRESHOLD_SECONDS):
        super().__init__(app)
        self.refresh_threshold = refresh_threshold_seconds

    async def dispatch(self, request: Request, call_next) -> Response:
        # exempt 路径: 跳过续期
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        token = _extract_token(request)
        if not token:
            return await call_next(request)

        # 解码 + 吊销检查
        try:
            payload = decode_token(token, check_revocation=True)
        except TokenValidationError:
            # token 已无效, 让下游依赖 (get_current_user) 抛 401
            return await call_next(request)

        # 检查是否临近过期
        exp = payload.get("exp")
        now = int(time.time())
        if exp is None or (exp - now) > self.refresh_threshold:
            # 剩余时间充足, 不续期
            return await call_next(request)

        # 续期
        try:
            new_token = self._renew(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning("token_refresh: failed to renew, err=%r", e)
            return await call_next(request)

        # 解析新 token 的 exp (用于响应头)
        try:
            new_payload = decode_token(new_token, check_revocation=False)
            new_exp = new_payload.get("exp")
        except Exception:
            new_exp = None

        # 透传业务响应
        response = await call_next(request)
        if new_token:
            response.headers[_RENEWED_TOKEN_HEADER] = new_token
            if new_exp:
                response.headers[_RENEWED_EXPIRES_HEADER] = str(new_exp)
        return response

    def _renew(self, old_payload: dict) -> str:
        """根据旧 payload 签发新 token (保留 sub/role, 重新生成 jti/iat/exp)"""
        sub = old_payload.get("sub")
        username = old_payload.get("username")
        role = old_payload.get("role")
        if not sub:
            raise ValueError("missing sub in old payload")
        data = {"sub": sub}
        if username:
            data["username"] = username
        extra: dict = {}
        if role:
            extra["role"] = role
        return create_access_token(data=data, extra_claims=extra or None)


# ============== Stage 5.2 / Phase-B: MiddlewareRegistry 工厂入口 ==============

def token_refresh_factory(app: FastAPI) -> None:
    """Token 续期中间件工厂 (供 MiddlewareRegistry 调用)

    注册到 MiddlewareRegistry 时, order 推荐 35 (在 request_id 之后, request_timing 之前),
    确保能拿到 rid 写入审计日志.
    """
    app.add_middleware(TokenRefreshMiddleware)


__all__ = [
    "TokenRefreshMiddleware",
    "DEFAULT_REFRESH_THRESHOLD_SECONDS",
    "token_refresh_factory",
]
