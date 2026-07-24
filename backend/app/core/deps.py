"""
兼容垫片 (Phase 1.5): 从 app.core.deps 转发到 app.middleware.http.auth
=====================================================================

历史路径, 新代码请直接 `from app.middleware.http.auth import ...`
本文件将在 Stage 2 完成后删除 (参见 [3层架构重构执行计划.md])
"""
# ruff: noqa: F401,F403
from app.middleware.http.auth import (  # noqa: F401
    oauth2_scheme,
    get_current_user,
    require_admin,
    get_user_optional_for_query,
)
