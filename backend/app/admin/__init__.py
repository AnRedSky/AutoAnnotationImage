"""
Admin Application — 业务应用 1/4
================================

**职责**:
- 用户管理 (CRUD / 角色 / 密码重置)
- 统计概览 (全局/数据集维度)
- 系统级接口 (health / system info)

**应用特征**:
- 名称: admin
- 版本: 3.0.0
- API 前缀: /api/users, /api/stats, /api (system 路由)
- 依赖: app.common, app.database, app.middleware, app.utils

**目录约定**:
- api/    路由层 (FastAPI APIRouter)
- service/ 业务编排 (AdminService, UserService, StatsService)
- model/  ORM 模型 (User, AuditLog, Role — 未来)
- schema/ Pydantic DTO (UserIn/Out, StatsOut)
- repository/ 复杂查询 (user_repo, stats_repo)
- events.py 订阅领域事件 (UserCreated → 初始化默认数据集)

**v3.0.0 Stage 2 新增**: 多应用架构骨架
"""
from fastapi import APIRouter

from app.common.interfaces import AppInterface
from app.registry import AppRegistry


class AdminApp(AppInterface):
    """Admin 应用 — 用户/角色/审计/统计"""

    @property
    def name(self) -> str:
        return "admin"

    @property
    def version(self) -> str:
        return "3.0.0"

    @property
    def router(self) -> APIRouter:
        # Stage 2.3-2.5 后, 这里会合并 user/stats/system 的子路由
        return _admin_router

    def register_events(self) -> list[str]:
        return [
            "user.created",  # 新用户创建 → 初始化默认数据集
            "user.deleted",  # 用户删除 → 清理其资源
            "user.updated",  # 用户信息更新
        ]


# ============== 根路由 (Stage 2.5 之后会聚合子模块) ==============
_admin_router = APIRouter()

# Stage 2.3-2.5 完成后, 这里会类似:
# from app.admin.api.user import router as user_router
# from app.admin.api.stats import router as stats_router
# from app.admin.api.system import router as system_router
# _admin_router.include_router(user_router, prefix="/users", tags=["用户管理"])
# _admin_router.include_router(stats_router, prefix="/stats", tags=["统计分析"])
# _admin_router.include_router(system_router, tags=["系统"])


# ============== 应用注册 ==============
# 导入时自动注册, main.py 通过 AppRegistry.discover_apps() 触发
AppRegistry.register(AdminApp())


__all__ = ["AdminApp", "admin_app"]

# 便捷引用: app.admin.admin_app
admin_app = AdminApp()
