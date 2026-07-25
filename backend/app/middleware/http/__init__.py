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
    # 工具函数
    "get_request_id",
    "set_request_id",
    # Stage 5.2 工厂入口 (供 MiddlewareRegistry 调用)
    "cors_factory",
    "error_handler_factory",
    "request_id_factory",
    "request_timing_factory",
]


# ============================================================
#  Stage 5.2: 注册中间件到 MiddlewareRegistry
# ============================================================
# 在 import 阶段注册 4 个中间件条目, 这样 main.py 调用 MiddlewareRegistry.discover()
# 时会触发本 __init__ 的执行, 进而触发下方的 MiddlewareRegistry.register() 调用.
#
# 顺序约定 (order 升序 = 从内到外):
#   10: CORS            - 最内层, 路由直接看到 CORS header
#   20: error_handler   - 在 RequestID 之后, 确保异常日志附带 rid
#   30: request_id      - 让外层中间件能读到 rid
#   40: request_timing  - 最外层, 记录整体耗时
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
        name="request_id",
        factory=request_id_factory,
        order=30,
        description="请求 ID 注入/透传 (X-Request-ID, 用于跨服务追踪)",
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
