"""
Request Timing Middleware (HTTP Layer)
======================================

记录每个 HTTP 请求的耗时, 用于:
- 慢请求自动告警 (>阈值时输出 WARNING 日志)
- 性能瓶颈定位 (平均响应时间 / P95 / P99)
- 配合 RequestIDMiddleware, 慢请求日志附带 request_id 便于排查

**v3.0.0 Stage 3 新增**.

**阈值配置**:
- 警告阈值: 500ms (默认, 可在 .env 中调整 REQUEST_SLOW_THRESHOLD_MS)
- 总是输出 DEBUG 日志 (含 method/path/status/duration_ms)

**使用示例**:
- 启动时自动注册, 无需手动配置
- 日志中查找 `[SLOW REQUEST]` 即可找到所有慢请求
"""
import logging
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.middleware.http.request_id import get_request_id

logger = logging.getLogger("app.middleware.request_timing")

# 慢请求默认阈值 (毫秒)
DEFAULT_SLOW_THRESHOLD_MS = 500


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """请求耗时中间件

    - 每个请求开始记录 start_time
    - 请求结束计算 duration_ms, 写入响应 header (X-Response-Time)
    - 慢请求 (>阈值) 输出 WARNING 日志
    - 所有请求输出 DEBUG 日志 (生产可关闭)
    """

    def __init__(self, app, slow_threshold_ms: Optional[int] = None):
        super().__init__(app)
        threshold = slow_threshold_ms
        if threshold is None:
            threshold = getattr(settings, "REQUEST_SLOW_THRESHOLD_MS", DEFAULT_SLOW_THRESHOLD_MS)
        self.slow_threshold_ms = threshold

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        rid = get_request_id() or "no-rid"
        method = request.method
        path = request.url.path

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                f"[rid={rid}] EXCEPTION on {method} {path} after {duration_ms:.1f}ms"
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        status_code = response.status_code

        # 响应 header (前端可读取)
        response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"

        # 日志记录
        log_msg = (
            f"[rid={rid}] {method} {path} -> {status_code} "
            f"in {duration_ms:.1f}ms"
        )
        if duration_ms >= self.slow_threshold_ms:
            logger.warning(f"[SLOW REQUEST] {log_msg}")
        else:
            logger.debug(log_msg)

        return response


__all__ = ["RequestTimingMiddleware", "DEFAULT_SLOW_THRESHOLD_MS"]
