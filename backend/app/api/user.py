"""
兼容垫片 (Stage 2.5): user API
=============================

**v3.0.0 Stage 2.5 迁移**: user 路由已迁入 app.admin.api.user
"""
from app.admin.api.user import router  # noqa: F401


__all__ = ["router"]
