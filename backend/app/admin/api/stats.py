"""
Stats API (app/admin/api/) — 统计概览 (Admin)
============================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/stats.py 迁入 admin 应用 (过渡: re-export)
"""
# 过渡: 从 app.api.stats 导入, 后续 Stage 5 将完整迁移
from app.api.stats import router  # noqa: F401


__all__ = ["router"]
