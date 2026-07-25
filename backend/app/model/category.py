"""
兼容垫片 (Stage 2.3): Category ORM Model
=======================================

**v3.0.0 Stage 2.3 迁移**: Category 已迁入 app.tasks.model.category
"""
from app.tasks.model.category import *  # noqa: F401,F403
from app.tasks.model.category import Category  # noqa: F401


__all__ = ["Category"]
