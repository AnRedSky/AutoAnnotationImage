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
**v3.0.0 Stage 2.7**: get_routes() 返回多 prefix 路由条目, main.py 自动挂载
"""
from fastapi import APIRouter

from app.common.interfaces import AppInterface, RouteEntry
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
        # 默认聚合根 (空), Stage 2.7 推荐使用 get_routes() 多 prefix 方案
        return _admin_router

    def get_routes(self) -> list[RouteEntry]:
        """返回 4 个路由条目: user / stats / system / tenant

        system 路由挂在 /api (而不是 /api/admin), 因为它是无鉴权的系统级端点.
        tenant 路由挂在 /api/tenants (v3.2.0 MT-7 多租户管理).

        注意: app.{name}.api 包的 __init__ 已经把每个子 router 重新导出为同名属性
        (user / system / stats / tenant), 因此 user_api 本身就是 APIRouter, 无需 .router
        """
        from app.admin.api import user as user_api
        from app.admin.api import stats as stats_api
        from app.admin.api import system as system_api
        from app.admin.api import team as team_api  # v3.3.0
        return [
            RouteEntry(user_api, "/api/users", ["用户管理"]),
            RouteEntry(stats_api, "/api/stats", ["统计分析"]),
            RouteEntry(system_api, "/api", ["系统"]),
            RouteEntry(team_api, "/api/teams", ["团队管理"]),  # v3.3.0
        ]

    def register_events(self) -> list[str]:
        return [
            "user.created",  # 新用户创建 → 初始化默认数据集
            "user.deleted",  # 用户删除 → 清理其资源
            "user.updated",  # 用户信息更新
        ]


# ============== 根路由 (兼容旧 default get_routes 实现) ==============
_admin_router = APIRouter()


# ============== 应用注册 ==============
# 导入时自动注册, main.py 通过 AppRegistry.discover_apps() 触发
AppRegistry.register(AdminApp())


__all__ = ["AdminApp", "admin_app"]

# 便捷引用: app.admin.admin_app
admin_app = AdminApp()
