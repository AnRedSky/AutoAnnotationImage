"""
兼容垫片 (Stage 2.4): StatsService
================================

**v3.0.0 Stage 2.4 迁移**: StatsService 已迁入 app.admin.service.stats_service
"""
from app.admin.service.stats_service import *  # noqa: F401,F403
from app.admin.service.stats_service import StatsService  # noqa: F401


__all__ = ["StatsService"]
