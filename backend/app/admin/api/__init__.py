"""
Admin API Package (Stage 2.5 填充)
=================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/* 迁入 admin 应用

**当前状态**:
- 已完整迁移: user, system
- 过渡引用: stats
"""
# Stage 2.5-2.8 完整迁移
from app.admin.api.user import router as user_router
from app.admin.api.system import router as system_router
from app.admin.api.stats import router as stats_router

# 命名导出
user = user_router  # type: ignore
system = system_router  # type: ignore
stats = stats_router  # type: ignore


__all__ = [
    "user", "system", "stats",
    "user_router", "system_router", "stats_router",
]
