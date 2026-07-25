"""
兼容垫片 (Stage 2.4): user_queries
==================================

**v3.0.0 Stage 2.4 迁移**: user_queries 已迁入 app.admin.repository.user_queries
"""
from app.admin.repository.user_queries import *  # noqa: F401,F403
from app.admin.repository.user_queries import (
    get_user_by_id,
    get_user_by_username,
    list_active_users,
    list_admins,
)  # noqa: F401


__all__ = [
    "get_user_by_id",
    "get_user_by_username",
    "list_active_users",
    "list_admins",
]
