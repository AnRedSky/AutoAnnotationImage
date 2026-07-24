"""Pydantic Schemas: Common (v3.0.0 Phase 4 新增)

通用响应 / 错误 / 分页 schema, 供所有 API 复用.
"""
from typing import Optional, Generic, TypeVar, List
from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """统一错误详情 (含 trace_id 供排查)"""
    code: str = Field(..., description="业务错误码, e.g. DATASET_NOT_FOUND")
    message: str = Field(..., description="人类可读的错误描述")
    field: Optional[str] = Field(None, description="错误字段 (校验错误时)")
    trace_id: Optional[str] = Field(None, description="请求追踪 ID")


class ErrorResponse(BaseModel):
    """统一错误响应 (Phase 1 已引入 AppException, 4.6 全面接入)"""
    success: bool = False
    error: ErrorDetail


class SuccessResponse(BaseModel):
    """统一成功响应 (用于无需 data 的端点)"""
    success: bool = True
    message: str = "OK"


class PaginationRequest(BaseModel):
    """通用分页请求 (供 query 参数)"""
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页响应 (含 total / items)"""
    total: int
    page: int
    page_size: int
    items: List[T]


class BatchOperationResult(BaseModel):
    """批量操作结果 (用于批量删除 / 批量激活)"""
    success: bool = True
    affected: int = 0
    failed: List[int] = Field(default_factory=list, description="失败项 id 列表")
    errors: List[str] = Field(default_factory=list, description="失败原因")


__all__ = [
    "ErrorDetail",
    "ErrorResponse",
    "SuccessResponse",
    "PaginationRequest",
    "PaginatedResponse",
    "BatchOperationResult",
]
