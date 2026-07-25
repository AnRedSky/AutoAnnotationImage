"""
兼容垫片 (Stage 2.5): auth API
=============================

**v3.0.0 Stage 2.5 迁移**: auth 路由已迁入 app.auth.api.auth
"""
from app.auth.api.auth import router  # noqa: F401


__all__ = ["router"]
