"""
兼容垫片 (Stage 2.5): system API
==============================

**v3.0.0 Stage 2.5 迁移**: system 路由已迁入 app.admin.api.system
"""
from app.admin.api.system import router  # noqa: F401


__all__ = ["router"]
