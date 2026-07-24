"""
统一业务异常体系 (Common Layer)
================================

目的:
- 替代散落各处的 `raise HTTPException(status_code=..., detail=...)` 手写风格,
  让业务代码 `raise NotFoundError("Dataset not found")` 即可, 由全局 handler
  统一转换为一致的 JSON 响应 `{code, message}`.
- 全局兜底未捕获异常, 避免把内部堆栈/错误细节泄漏给前端.

用法:
    from app.common.exceptions import NotFoundError, PermissionDeniedError
    raise NotFoundError("Dataset not found")
    raise PermissionDeniedError("No permission to access this dataset")

迁移策略: 现有 HTTPException 仍由 FastAPI 默认 handler 处理, 不破坏兼容;
新代码逐步改用本体系. 全局 Exception handler 仅兜底真正未捕获的异常.

v3.0.0 迁移: 从 app.core.exceptions 迁入 app.common.exceptions (Phase 1.3)
"""
from typing import Optional


class AppException(Exception):
    """业务异常基类"""

    status_code: int = 400
    code: str = "APP_ERROR"

    def __init__(self, message: str, code: Optional[str] = None):
        self.message = message
        if code:
            self.code = code
        super().__init__(message)


class NotFoundError(AppException):
    """资源不存在 (404)"""

    status_code = 404
    code = "NOT_FOUND"


class PermissionDeniedError(AppException):
    """无权限访问 (403)"""

    status_code = 403
    code = "PERMISSION_DENIED"


class ValidationError(AppException):
    """业务校验失败 (422)"""

    status_code = 422
    code = "VALIDATION_ERROR"


class ConflictError(AppException):
    """资源冲突 (409)"""

    status_code = 409
    code = "CONFLICT"


def to_response_payload(exc: AppException) -> dict:
    """将业务异常转换为统一 JSON 响应体"""
    return {"code": exc.code, "message": exc.message}
