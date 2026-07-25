"""
兼容垫片 (Stage 2.3): Base Class
================================

**v3.0.0 Stage 2.3 迁移**: Base 已迁入 app.common.base_model
"""
from app.common.base_model import *  # noqa: F401,F403
from app.common.base_model import Base  # noqa: F401


__all__ = ["Base"]
