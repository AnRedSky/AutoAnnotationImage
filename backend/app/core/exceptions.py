"""
兼容垫片 (Phase 1.3): 从 app.core.exceptions 转发到 app.common.exceptions
=======================================================================

历史路径, 新代码请直接 `from app.common.exceptions import ...`
本文件将在 Stage 2 完成后删除 (参见 [3层架构重构执行计划.md])
"""
# ruff: noqa: F401,F403
from app.common.exceptions import (  # noqa: F401
    AppException,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    ConflictError,
    to_response_payload,
)
