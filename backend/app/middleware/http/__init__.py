"""
Middleware HTTP Subpackage
==========================
FastAPI 依赖注入 / 中间件 / 异常处理

**Stage 3 新增**:
- `RequestIDMiddleware`: 注入/透传 X-Request-ID, 用于跨服务追踪
- `RequestTimingMiddleware`: 记录请求耗时, 慢请求自动告警
"""
from app.middleware.http.auth import (  # noqa: F401
    get_current_user,
    require_admin,
    get_user_optional_for_query,
    oauth2_scheme,
)
from app.middleware.http.cors import setup_cors  # noqa: F401
from app.middleware.http.error_handler import register_error_handlers  # noqa: F401
from app.middleware.http.request_id import (  # noqa: F401
    RequestIDMiddleware,
    get_request_id,
    set_request_id,
)
from app.middleware.http.request_timing import (  # noqa: F401
    RequestTimingMiddleware,
    DEFAULT_SLOW_THRESHOLD_MS,
)

__all__ = [
    "get_current_user",
    "require_admin",
    "get_user_optional_for_query",
    "oauth2_scheme",
    "setup_cors",
    "register_error_handlers",
    "RequestIDMiddleware",
    "get_request_id",
    "set_request_id",
    "RequestTimingMiddleware",
    "DEFAULT_SLOW_THRESHOLD_MS",
]
