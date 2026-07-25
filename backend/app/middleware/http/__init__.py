"""
Middleware HTTP Subpackage
==========================
FastAPI 依赖注入 / 中间件 / 异常处理

**Stage 3 新增**:
- `RequestIDMiddleware`: 注入/透传 X-Request-ID, 用于跨服务追踪
- `RequestTimingMiddleware`: 记录请求耗时, 慢请求自动告警

**Stage 5.2 新增**:
- 通过 `MiddlewareRegistry.register()` 集中注册 4 个中间件条目
  (cors / error_handler / request_id / request_timing), 供 main.py 通过
  `MiddlewareRegistry.apply(app)` 一行调用
- 中间件顺序由 order 字段控制: 10 (CORS) → 20 (error_handler) → 30 (request_id) → 40 (request_timing)

**Phase-B 新增** (JWT 鉴权完善):
- `rate_limit_factory`: 限流中间件 (order=25, 在 error_handler 之前)
- `auth_audit_factory`: 认证审计中间件 (order=22, 在 error_handler 之后)
- `token_refresh_factory`: Token 自动续期中间件 (order=35, 在 request_id 之后)
- 完整中间件链: 10 cors → 20 error_handler → 22 auth_audit → 25 rate_limit
                → 30 request_id → 35 token_refresh → 40 request_timing
"""
from app.middleware.http.auth import (  # noqa: F401
    get_current_user,
    require_admin,
    get_user_optional_for_query,
    oauth2_scheme,
)
from app.middleware.http.cors import setup_cors, cors_factory  # noqa: F401
from app.middleware.http.error_handler import (  # noqa: F401
    register_error_handlers,
    error_handler_factory,
)
from app.middleware.http.rate_limit import (  # noqa: F401
    rate_limit_factory,
    RATE_LIMIT_PROFILES,
    RateLimitProfile,
)
from app.middleware.http.auth_audit import (  # noqa: F401
    auth_audit_factory,
    AuthEventType,
    AuthAuditEvent,
    emit_audit_event,
)
from app.middleware.http.request_id import (  # noqa: F401
    RequestIDMiddleware,
    get_request_id,
    set_request_id,
    request_id_factory,
)
from app.middleware.http.request_timing import (  # noqa: F401
    RequestTimingMiddleware,
    DEFAULT_SLOW_THRESHOLD_MS,
    request_timing_factory,
)
from app.middleware.http.token_refresh import (  # noqa: F401
    token_refresh_factory,
    TokenRefreshMiddleware,
    DEFAULT_REFRESH_THRESHOLD_SECONDS,
)

__all__ = [
    # Auth (FastAPI Depends, 非 Starlette middleware)
    "get_current_user",
    "require_admin",
    "get_user_optional_for_query",
    "oauth2_scheme",
    # 旧版兼容 API (保留以防外部代码直接调用)
    "setup_cors",
    "register_error_handlers",
    # Stage 3 中间件类
    "RequestIDMiddleware",
    "RequestTimingMiddleware",
    "DEFAULT_SLOW_THRESHOLD_MS",
    # Stage 5.2 工厂入口 (供 MiddlewareRegistry 调用)
    "cors_factory",
    "error_handler_factory",
    "request_id_factory",
    "request_timing_factory",
    # Phase-B: 限流 / 审计 / 续期
    "rate_limit_factory",
    "RATE_LIMIT_PROFILES",
    "RateLimitProfile",
    "auth_audit_factory",
    "AuthEventType",
    "AuthAuditEvent",
    "emit_audit_event",
    "token_refresh_factory",
    "TokenRefreshMiddleware",
    "DEFAULT_REFRESH_THRESHOLD_SECONDS",
    # 工具函数
    "get_request_id",
    "set_request_id",
]


# ============================================================
#  Stage 5.2: 注册中间件到 MiddlewareRegistry
# ============================================================
# 在 import 阶段注册所有中间件条目, 这样 main.py 调用 MiddlewareRegistry.discover()
# 时会触发本 __init__ 的执行, 进而触发下方的 MiddlewareRegistry.register() 调用.
#
# 完整顺序约定 (order 升序 = 从内到外):
#   10: CORS              - 最内层, 路由直接看到 CORS header
#   20: error_handler     - 在 RequestID 之后, 确保异常日志附带 rid
#   22: auth_audit        - 在 error_handler 之后, 捕获 401/403 并审计
#   25: rate_limit        - 在 error_handler 之后, 提前拦截高频请求
#   30: request_id        - 让外层中间件能读到 rid
#   35: token_refresh     - 在 request_id 之后, 续期事件可携带 rid
#   40: request_timing    - 最外层, 记录整体耗时
#
# 注意: auth.py 是 FastAPI Depends 依赖, 不是 Starlette middleware, 不参与此处注册.
from app.common.interfaces import MiddlewareEntry
from app.registry import MiddlewareRegistry  # noqa: E402

# 使用模块级变量, 避免 lint 警告未使用
_ = MiddlewareEntry

_MIDDLEWARE_ENTRIES = (
    MiddlewareEntry(
        name="cors",
        factory=cors_factory,
        order=10,
        description="CORS 跨域配置 (含生产校验 + 互斥降级)",
    ),
    MiddlewareEntry(
        name="error_handler",
        factory=error_handler_factory,
        order=20,
        description="全局异常处理 (AppException + 兜底 500)",
    ),
    MiddlewareEntry(
        name="auth_audit",
        factory=auth_audit_factory,
        order=22,
        description="认证事件审计 (login/登出/改密/Token 拒绝 事件日志, Phase-B)",
    ),
    MiddlewareEntry(
        name="rate_limit",
        factory=rate_limit_factory,
        order=25,
        description="限流 (Redis 滑动窗口, login/register/change_password/global, Phase-B)",
    ),
    MiddlewareEntry(
        name="request_id",
        factory=request_id_factory,
        order=30,
        description="请求 ID 注入/透传 (X-Request-ID, 用于跨服务追踪)",
    ),
    MiddlewareEntry(
        name="token_refresh",
        factory=token_refresh_factory,
        order=35,
        description="Token 自动续期 (临近过期通过 X-Renewed-Token 静默续签, Phase-B)",
    ),
    MiddlewareEntry(
        name="request_timing",
        factory=request_timing_factory,
        order=40,
        description="请求耗时记录 + 慢请求告警 (X-Response-Time)",
    ),
)

for _entry in _MIDDLEWARE_ENTRIES:
    MiddlewareRegistry.register(_entry)

del _entry
del _MIDDLEWARE_ENTRIES
