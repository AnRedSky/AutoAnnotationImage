"""
限流中间件 (HTTP Layer)
========================

v3.0.0 Phase-B 新增: 基于 Redis 滑动窗口的限流中间件

**核心功能**:
- 路径级限流: 针对特定路径 (如 /api/auth/login) 限流
- 维度: IP / username (从请求头/表单提取)
- 算法: 滑动窗口 (Sorted Set, 比固定窗口更平滑)
- 降级: Redis 不可用时降级为不限制, 避免 Redis 故障导致全站不可用

**使用方式**:
```python
# 1) 全局限流 (按 IP)
from app.middleware.http.rate_limit import rate_limit_factory, RATE_LIMIT_PROFILES

app.add_middleware(RateLimitMiddleware, profile=RATE_LIMIT_PROFILES["global"])

# 2) 在 MiddlewareRegistry 注册
MiddlewareRegistry.register(MiddlewareEntry(
    name="rate_limit", factory=rate_limit_factory, order=25,
    description="限流 (Redis 滑动窗口)"
))
```

**预置策略** (RATE_LIMIT_PROFILES):
- login: 5 次/min, 按 IP+username 限流 (防暴力破解)
- register: 3 次/h, 按 IP 限流 (防批量注册)
- change_password: 5 次/h, 按 username 限流 (防爆破)
- global: 120 次/min, 按 IP 限流 (通用保护)
- strict: 10 次/min, 按 IP 限流 (敏感接口)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.database.redis import redis_client

logger = logging.getLogger("app.middleware.rate_limit")

# ============== 限流配置 ==============

# Redis key 前缀
_RATE_PREFIX = "rl:"


def _k(scope: str, key: str) -> str:
    return f"{_RATE_PREFIX}{scope}:{key}"


@dataclass
class RateLimitProfile:
    """限流策略配置

    Attributes:
        name: 策略名, 用于日志/调试
        max_requests: 时间窗口内最大请求数
        window_seconds: 时间窗口 (秒)
        scope: 限流维度 ("ip" / "username" / "ip_username")
        paths: 应用的路径前缀列表, None = 全部
        method: 限定 HTTP 方法, None = 全部
        block_status_code: 触发限流时返回的状态码
        enabled: 是否启用
    """
    name: str
    max_requests: int
    window_seconds: int
    scope: str = "ip"  # ip | username | ip_username
    paths: Optional[list[str]] = None
    method: Optional[str] = None
    block_status_code: int = 429
    enabled: bool = True


# ============== 预置策略 ==============

RATE_LIMIT_PROFILES: dict[str, RateLimitProfile] = {
    # 全局通用: 防止接口被高频刷 (DDoS 基础防护)
    "global": RateLimitProfile(
        name="global",
        max_requests=600,
        window_seconds=60,
        scope="ip",
        enabled=True,
    ),
    # 登录: 防密码爆破
    "login": RateLimitProfile(
        name="login",
        max_requests=60,
        window_seconds=60,
        scope="ip_username",
        paths=["/api/auth/login"],
        method="POST",
        block_status_code=429,
        enabled=True,
    ),
    # 注册: 防批量注册
    "register": RateLimitProfile(
        name="register",
        max_requests=60,
        window_seconds=60,
        scope="ip",
        paths=["/api/auth/register"],
        method="POST",
        block_status_code=429,
        enabled=True,
    ),
    # 改密: 防爆破
    "change_password": RateLimitProfile(
        name="change_password",
        max_requests=30,
        window_seconds=60,
        scope="username",
        paths=["/api/auth/change-password"],
        method="POST",
        block_status_code=429,
        enabled=True,
    ),
    # 严格: 敏感操作
    "strict": RateLimitProfile(
        name="strict",
        max_requests=60,
        window_seconds=60,
        scope="ip",
        block_status_code=429,
        enabled=True,
    ),
}


# ============== 限流核心 (滑动窗口) ==============

def _extract_client_ip(request: Request) -> str:
    """提取客户端 IP (考虑 X-Forwarded-For / X-Real-IP 反向代理头)"""
    xff = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if xff:
        return xff
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    # FastAPI/Starlette 的 client 是 (host, port) 元组
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def _extract_username_from_form(request: Request) -> Optional[str]:
    """从表单 body 中提取 username (用于 login 限流)

    注意: 这是 best-effort, 取不到时降级为仅 IP 限流
    """
    try:
        # OAuth2PasswordRequestForm 提交 application/x-www-form-urlencoded
        # 仅当 Content-Type 是 form 类型时才解析, 避免其它请求被强制读 body
        ct = request.headers.get("Content-Type", "").lower()
        if "form" not in ct and "urlencoded" not in ct:
            return None
        # request.form() 只能调用一次, 这里用 receive() 拷贝后再解析
        body = await request.body()
        # 简单解析: form 编码为 key=value&key2=value2
        from urllib.parse import parse_qs
        params = parse_qs(body.decode("utf-8", errors="ignore"))
        # OAuth2 表单: username=xxx&password=yyy
        for key in ("username", "email"):
            vals = params.get(key)
            if vals and vals[0]:
                return vals[0]
    except Exception:  # noqa: BLE001
        return None
    return None


def _make_redis_key(profile: RateLimitProfile, request: Request, username: Optional[str]) -> str:
    """生成 Redis key (scope 决定维度)"""
    if profile.scope == "ip":
        return _k(profile.name, _extract_client_ip(request))
    if profile.scope == "username":
        if not username:
            username = "_anon_"
        return _k(profile.name, f"u:{username}")
    if profile.scope == "ip_username":
        ip = _extract_client_ip(request)
        u = username or "_anon_"
        return _k(profile.name, f"{ip}|u:{u}")
    return _k(profile.name, "default")


def _check_and_increment(profile: RateLimitProfile, redis_key: str) -> tuple[bool, int, int]:
    """滑动窗口算法检查 (Sorted Set 实现)

    Returns:
        (allowed, current_count, retry_after_seconds)

    算法:
        1. ZREMRANGEBYSCORE: 删除窗口外的旧记录
        2. ZCARD: 当前窗口内请求数
        3. 若超限 → 返回 (False, count, retry_after)
        4. 否则 ZADD: 记录本次请求, EXPIRE 兜底
    """
    now = int(time.time())
    window_start = now - profile.window_seconds
    try:
        pipe = redis_client.pipeline()
        # 1) 删除窗口外的旧记录
        pipe.zremrangebyscore(redis_key, 0, window_start)
        # 2) 当前窗口内请求数
        pipe.zcard(redis_key)
        results = pipe.execute()
        current = int(results[1])
        if current >= profile.max_requests:
            # 超限: 计算最早的一条记录, 多久后窗口滑出
            oldest = redis_client.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                oldest_ts = int(oldest[0][1])
                retry_after = max(1, (oldest_ts + profile.window_seconds) - now)
            else:
                retry_after = profile.window_seconds
            return False, current, retry_after
        # 3) 未超限: 记录本次请求
        # 用 microsecond 精度避免同秒多请求 zset member 冲突
        member = f"{now}-{now * 1000 + (current % 1000)}"
        pipe = redis_client.pipeline()
        pipe.zadd(redis_key, {member: now})
        # TTL = 窗口 + 缓冲, 避免冷 key 残留
        pipe.expire(redis_key, profile.window_seconds + 60)
        pipe.execute()
        return True, current + 1, 0
    except Exception as e:  # noqa: BLE001
        # Redis 失败: 降级放行, 避免 Redis 故障导致全站不可用
        logger.warning("rate_limit: redis failed, key=%s err=%r", redis_key, e)
        return True, 0, 0


# ============== 中间件 ==============

class RateLimitMiddleware(BaseHTTPMiddleware):
    """限流中间件

    用法:
        app.add_middleware(
            RateLimitMiddleware,
            profile=RATE_LIMIT_PROFILES["global"],
        )
    """

    def __init__(self, app, profile: RateLimitProfile):
        super().__init__(app)
        self.profile = profile

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.profile.enabled:
            return await call_next(request)

        # 路径过滤
        if self.profile.paths:
            path = request.url.path
            if not any(path.startswith(p) for p in self.profile.paths):
                return await call_next(request)

        # 方法过滤
        if self.profile.method and request.method != self.profile.method.upper():
            return await call_next(request)

        # 提取 username (如果需要)
        username: Optional[str] = None
        if self.profile.scope in ("username", "ip_username"):
            username = await _extract_username_from_form(request)
        redis_key = _make_redis_key(self.profile, request, username)
        allowed, current, retry_after = _check_and_increment(self.profile, redis_key)
        if not allowed:
            logger.warning(
                "rate_limit: blocked path=%s key=%s current=%d/%d retry_after=%ds",
                request.url.path, redis_key, current,
                self.profile.max_requests, retry_after,
            )
            return JSONResponse(
                status_code=self.profile.block_status_code,
                content={
                    "code": "rate_limited",
                    "message": f"请求过于频繁, 请 {retry_after} 秒后重试",
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.profile.max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )
        response = await call_next(request)
        # 透传限流信息 (X-RateLimit-Remaining 等)
        remaining = max(0, self.profile.max_requests - current)
        response.headers["X-RateLimit-Limit"] = str(self.profile.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


# ============== Stage 5.2 / Phase-B: MiddlewareRegistry 工厂入口 ==============

# 路径感知的 profile 选择器
def _select_profile(request: Request) -> Optional[RateLimitProfile]:
    """根据请求路径选择匹配的 profile

    优先级: login > register > change_password > global
    """
    path = request.url.path
    if path.startswith("/api/auth/login") and request.method == "POST":
        return RATE_LIMIT_PROFILES.get("login")
    if path.startswith("/api/auth/register") and request.method == "POST":
        return RATE_LIMIT_PROFILES.get("register")
    if path.startswith("/api/auth/change-password") and request.method == "POST":
        return RATE_LIMIT_PROFILES.get("change_password")
    return RATE_LIMIT_PROFILES.get("global")


# 全局限流中间件 (按路径动态选 profile)
class PathAwareRateLimitMiddleware(BaseHTTPMiddleware):
    """路径感知的限流中间件 (单一中间件, 内部按路径选 profile)

    优势:
    - 避免对每个 profile 都加一个中间件 (减少中间件栈深度)
    - 路径匹配集中, 易于维护
    """

    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        profile = _select_profile(request)
        if profile is None or not profile.enabled:
            return await call_next(request)

        # 路径/方法过滤
        if profile.method and request.method != profile.method.upper():
            return await call_next(request)

        username: Optional[str] = None
        if profile.scope in ("username", "ip_username"):
            username = await _extract_username_from_form(request)
        redis_key = _make_redis_key(profile, request, username)
        allowed, current, retry_after = _check_and_increment(profile, redis_key)
        if not allowed:
            logger.warning(
                "rate_limit: blocked path=%s profile=%s key=%s current=%d/%d retry_after=%ds",
                request.url.path, profile.name, redis_key, current,
                profile.max_requests, retry_after,
            )
            return JSONResponse(
                status_code=profile.block_status_code,
                content={
                    "code": "rate_limited",
                    "message": f"请求过于频繁, 请 {retry_after} 秒后重试",
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(profile.max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )
        response = await call_next(request)
        remaining = max(0, profile.max_requests - current)
        response.headers["X-RateLimit-Limit"] = str(profile.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


def rate_limit_factory(app: FastAPI) -> None:
    """限流中间件工厂 (供 MiddlewareRegistry 调用)

    注册到 MiddlewareRegistry 时, order 推荐 25 (在 CORS 之后, error_handler 之前).
    """
    app.add_middleware(PathAwareRateLimitMiddleware)


__all__ = [
    "RateLimitProfile",
    "RATE_LIMIT_PROFILES",
    "RateLimitMiddleware",
    "PathAwareRateLimitMiddleware",
    "rate_limit_factory",
    "_extract_client_ip",
]
