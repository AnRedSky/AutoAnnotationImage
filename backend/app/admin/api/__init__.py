"""Admin API Package — 路由层

Stage 2.5 后会从 app/api/{user,stats,system}.py 迁移至此.
"""
# 兼容垫片: 允许外部继续 `from app.admin.api import user_router` 风格
# (待 Stage 2.5 完成后激活)
__all__: list[str] = []
