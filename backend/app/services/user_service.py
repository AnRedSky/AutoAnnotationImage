"""
兼容垫片 (Stage 2.4): UserService
=================================

**v3.0.0 Stage 2.4 迁移**: UserService 已迁入 app.admin.service.user_service
"""
from app.admin.service.user_service import *  # noqa: F401,F403
from app.admin.service.user_service import UserService  # noqa: F401


__all__ = ["UserService"]
