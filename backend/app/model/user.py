"""
兼容垫片 (Stage 2.3): User ORM Model
=====================================

**v3.0.0 Stage 2.3 迁移**: User 已迁入 app.admin.model.user
此处仅做向后兼容 re-export, 老代码 `from app.model.user import User` 仍可工作.

新代码请直接:
    from app.admin.model.user import User
"""
from app.admin.model.user import *  # noqa: F401,F403
from app.admin.model.user import User  # noqa: F401


__all__ = ["User"]
