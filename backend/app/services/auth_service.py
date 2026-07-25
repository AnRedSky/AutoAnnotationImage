"""
兼容垫片 (Stage 2.4): AuthService
================================

**v3.0.0 Stage 2.4 迁移**: AuthService 已迁入 app.auth.service.auth_service
"""
from app.auth.service.auth_service import *  # noqa: F401,F403
from app.auth.service.auth_service import AuthService  # noqa: F401


__all__ = ["AuthService"]
