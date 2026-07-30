"""
Admin API Package (Stage 2.5 填充)
=================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/* 迁入 admin 应用
**v3.2.0 MT-7**: 新增 tenant 多租户管理 API

**当前状态**:
- 已完整迁移: user, system, tenant
- 过渡引用: stats
"""
# Stage 2.5-2.8 完整迁移
from app.admin.api.user import router as user_router
from app.admin.api.system import router as system_router
from app.admin.api.stats import router as stats_router
from app.admin.api.tenant import router as tenant_router  # v3.2.0 MT-7

# 命名导出
user = user_router  # type: ignore
system = system_router  # type: ignore
stats = stats_router  # type: ignore
tenant = tenant_router  # type: ignore


__all__ = [
    "user", "system", "stats", "tenant",
    "user_router", "system_router", "stats_router", "tenant_router",
]
