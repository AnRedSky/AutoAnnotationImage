"""
Request ID Middleware (HTTP Layer)
==================================

为每个 HTTP 请求注入/透传 X-Request-ID, 用于:
- 跨服务链路追踪 (前端 → API → Worker → DB/Redis)
- 日志关联 (一个请求的所有日志可以 grep 同一个 request_id)
- 错误排查 (前端报错时附带 request_id, 后端日志秒定位)

**v3.0.0 Stage 3 新增**.

**工作流程**:
1. 收到请求时, 优先从 `X-Request-ID` header 取 (前端传入)
2. 如果没有, 自动生成 `uuid4().hex`
3. 写入 `request.state.request_id` (供路由/服务访问)
4. 写入 ContextVar (供跨 async 调用的日志使用)
5. 响应时回传 `X-Request-ID` header

**使用示例**:
```python
from app.middleware.http.request_id import get_request_id

@router.get("/foo")
async def foo(request: Request):
    rid = get_request_id()  # 当前请求的 ID
    log.info(f"foo called, rid={rid}")
    return {"rid": rid}
```
"""
import logging
import uuid
from contextvars import ContextVar
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("app.middleware.request_id")

# ContextVar: 在 async 上下文中传递 request_id, 跨函数调用
_request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> Optional[str]:
    """获取当前请求的 ID (从 ContextVar 读取)

    Returns:
        request_id 字符串, 如果不在请求上下文中则返回 None
    """
    return _request_id_var.get()


def set_request_id(rid: str) -> None:
    """手动设置 request_id (供 worker 任务回写等场景)"""
    _request_id_var.set(rid)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """请求 ID 中间件

    - 透传前端 X-Request-ID header (前端 → 后端)
    - 自动生成 UUID4 (后端 → 前端)
    - 响应回传 X-Request-ID (后端 → 前端)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1) 优先取 header, 没有则生成
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex

        # 2) 写入 request.state (FastAPI 路由可见)
        request.state.request_id = rid

        # 3) 写入 ContextVar (跨 async 调用的服务/工具可见)
        token = _request_id_var.set(rid)

        try:
            # 4) 执行业务
            response = await call_next(request)
        except Exception:
            # 业务异常: 日志附带 request_id 便于排查
            logger.exception(f"[rid={rid}] unhandled exception on {request.method} {request.url.path}")
            raise
        finally:
            # 5) 恢复 ContextVar (避免污染后续请求)
            _request_id_var.reset(token)

        # 6) 响应 header 回传
        response.headers[REQUEST_ID_HEADER] = rid
        return response


__all__ = [
    "RequestIDMiddleware",
    "get_request_id",
    "set_request_id",
    "REQUEST_ID_HEADER",
]
