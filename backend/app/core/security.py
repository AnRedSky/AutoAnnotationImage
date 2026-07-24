"""
兼容垫片 (Phase 1.4): 从 app.core.security 转发到 app.middleware.security.security
==============================================================================

历史路径, 新代码请直接 `from app.middleware.security.security import ...`
本文件将在 Stage 2 完成后删除 (参见 [3层架构重构执行计划.md])
"""
# ruff: noqa: F401,F403
from app.middleware.security.security import (  # noqa: F401
    hash_password,
    get_password_hash,
    verify_password,
    create_access_token,
    decode_token,
)
