"""
Auth API Package (Stage 2.5 填充)
===============================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/* 迁入 auth 应用
"""
# Stage 2.5 完整迁移
from app.auth.api.auth import router as auth_router

# 命名导出
auth = auth_router  # type: ignore


__all__ = [
    "auth",
    "auth_router",
]
