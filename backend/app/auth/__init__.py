"""
Auth Application — 业务应用 2/4
===============================

**职责**:
- 用户登录 (username/password)
- 用户注册 (admin only)
- Token 签发 / 刷新 / 登出
- 密码哈希 / 校验
- 当前用户信息查询

**应用特征**:
- 名称: auth
- 版本: 3.0.0
- API 前缀: /api/auth
- 依赖: app.common, app.database, app.middleware, app.utils, app.admin.model.User

**目录约定**:
- api/    路由层 (auth.py)
- service/ 业务编排 (AuthService)
- schema/ Pydantic DTO (LoginRequest, TokenResponse, RegisterRequest)
- repository/ 复杂查询 (auth_repo)
- events.py 订阅领域事件

**重要约束**:
- 鉴权路由 (login/register) 必须无认证依赖 (open access)
- 不写死 User 模型, 通过 app.admin.model.User 引用
- Token 生成/校验委托 middleware/security/jwt (Stage 3 完善)

**v3.0.0 Stage 2 新增**: 多应用架构骨架
**v3.0.0 Stage 2.7**: get_routes() 返回单条路由
"""
from fastapi import APIRouter

from app.common.interfaces import AppInterface, RouteEntry
from app.registry import AppRegistry


class AuthApp(AppInterface):
    """Auth 应用 — 登录/注册/Token"""

    @property
    def name(self) -> str:
        return "auth"

    @property
    def version(self) -> str:
        return "3.0.0"

    @property
    def router(self) -> APIRouter:
        return _auth_router

    def get_routes(self) -> list[RouteEntry]:
        """返回 1 个路由条目: auth"""
        from app.auth.api import auth as auth_api
        return [
            RouteEntry(auth_api, "/api/auth", ["用户认证"]),
        ]

    def register_events(self) -> list[str]:
        return [
            "user.deleted",  # 清理该用户的所有 token/session
        ]


# ============== 根路由 (兼容旧 default get_routes 实现) ==============
_auth_router = APIRouter()


# ============== 应用注册 ==============
AppRegistry.register(AuthApp())


__all__ = ["AuthApp", "auth_app"]

# 便捷引用: app.auth.auth_app
auth_app = AuthApp()
