"""
全局异常处理器 (Middleware Layer)
==================================

v3.0.0 新增 (Phase 1.10): 从 app.main 内联提取
"""
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.common.exceptions import AppException, to_response_payload


def register_error_handlers(app: FastAPI) -> None:
    """注册全局异常处理

    - AppException: 业务异常, 转换为统一 {code, message}
    - Exception: 未捕获异常, 记录堆栈, 对前端仅返回脱敏的 500
    """

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        """业务异常: 转换为统一 {code, message} 响应"""
        logging.getLogger("app.main").warning(
            "AppException %s on %s %s: %s",
            exc.code, request.method, request.url.path, exc.message,
        )
        return JSONResponse(status_code=exc.status_code, content=to_response_payload(exc))

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """未捕获异常: 记录完整堆栈, 对前端仅返回脱敏的 500"""
        logging.getLogger("app.main").exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "内部错误，请联系管理员"},
        )
