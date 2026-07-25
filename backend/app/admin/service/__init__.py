"""
Admin Services Package (Stage 2.4 填充)
=======================================

**v3.0.0 Stage 2.4 迁移**: 从 app/services/* 迁入 admin 应用
"""
from app.admin.service.user_service import UserService
from app.admin.service.stats_service import StatsService

__all__ = ["UserService", "StatsService"]
