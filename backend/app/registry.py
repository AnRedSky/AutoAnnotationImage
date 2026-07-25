"""
应用注册中心 (App Layer)
========================

**职责**:
- 注册所有业务应用 (AppInterface 实现类)
- 提供应用路由的自动聚合 (main.py 用 iter_routes() 挂载)
- 提供应用启动/关闭钩子的统一调用 (lifespan 用 startup_all / shutdown_all)
- 提供按名查询单个应用 (e.g. AppRegistry.get("tasks"))

**使用方式**:
```python
# 在 app/tasks/__init__.py
from app.registry import AppRegistry
from app.common.interfaces import RouteEntry
from app.tasks.api import router

class TasksApp(AppInterface):
    name = "tasks"
    version = "1.0.0"
    @property
    def router(self): return router
    def get_routes(self):
        return [RouteEntry(dataset_router, "/api/datasets", ["数据集管理"]), ...]
    def register_events(self): return ["task.started", "task.completed"]
    async def startup(self): ...
    async def shutdown(self): ...

AppRegistry.register(TasksApp())
```

**自动发现**:
main.py 调用 `AppRegistry.discover_apps(["admin", "auth", "tasks", "annotation"])`,
会 import app/{name}/__init__.py 触发 register() 调用, 避免手写 import 列表.

**路由挂载** (Stage 2.7):
main.py 用 `for entry in AppRegistry.iter_routes(): app.include_router(entry.router, prefix=entry.prefix, tags=entry.tags)`,
无需手写 14 行 include_router.

v3.0.0 Stage 2 新增
v3.0.0 Stage 2.7: 新增 iter_routes() 辅助方法
"""
import logging
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from app.common.interfaces import AppInterface, RouteEntry

logger = logging.getLogger(__name__)


class AppRegistry:
    """业务应用注册中心 (单例)"""

    _apps: Dict[str, "AppInterface"] = {}
    _initialized: bool = False

    @classmethod
    def register(cls, app: "AppInterface") -> None:
        """注册一个应用

        Args:
            app: 实现 AppInterface 的应用实例
        """
        if app.name in cls._apps:
            logger.warning(f"AppRegistry: app '{app.name}' already registered, overwriting")
        cls._apps[app.name] = app
        logger.info(f"AppRegistry: registered app '{app.name}' v{app.version}")

    @classmethod
    def get(cls, name: str) -> Optional["AppInterface"]:
        """按名获取应用"""
        return cls._apps.get(name)

    @classmethod
    def all_apps(cls) -> List["AppInterface"]:
        """获取所有已注册应用 (按注册顺序)"""
        return list(cls._apps.values())

    @classmethod
    def all_routers(cls):
        """获取所有应用路由 (供 main.py 挂载, 旧 API 兼容)

        Deprecated: 推荐使用 iter_routes() 携带 prefix + tags.
        """
        return [app.router for app in cls._apps.values()]

    @classmethod
    def iter_routes(cls) -> List[Tuple[str, "RouteEntry"]]:
        """获取所有应用的路由条目 (app_name, RouteEntry) 列表

        Stage 2.7 新增: 返回 (应用名, RouteEntry) 元组, main.py 循环挂载.
        每个应用通过 AppInterface.get_routes() 返回自己的路由条目.

        Returns:
            List[(app_name, RouteEntry)]: 例如
              [("admin", RouteEntry(user_router, "/api/users", ["用户管理"])),
               ("admin", RouteEntry(stats_router, "/api/stats", ["统计分析"])),
               ("auth", RouteEntry(auth_router, "/api/auth", ["用户认证"])),
               ...]
        """
        result: List[Tuple[str, "RouteEntry"]] = []
        for app in cls._apps.values():
            try:
                entries = app.get_routes()
            except Exception:  # noqa: BLE001
                logger.exception(f"AppRegistry: app '{app.name}' get_routes() failed, skip")
                continue
            for entry in entries:
                result.append((app.name, entry))
        return result

    @classmethod
    async def startup_all(cls) -> None:
        """启动所有应用 (按注册顺序)"""
        for app in cls._apps.values():
            try:
                await app.startup()
                logger.info(f"AppRegistry: app '{app.name}' started")
            except Exception as e:
                logger.exception(f"AppRegistry: app '{app.name}' startup failed: {e!r}")
                raise

    @classmethod
    async def shutdown_all(cls) -> None:
        """关闭所有应用 (按注册反序)"""
        for app in reversed(list(cls._apps.values())):
            try:
                await app.shutdown()
                logger.info(f"AppRegistry: app '{app.name}' shutdown")
            except Exception as e:
                logger.exception(f"AppRegistry: app '{app.name}' shutdown failed: {e!r}")

    @classmethod
    def discover_apps(cls, app_names: Optional[List[str]] = None) -> None:
        """自动发现并导入应用 (触发 register 调用)

        Args:
            app_names: 要发现的应用列表 (None = 发现所有标准应用)
        """
        if app_names is None:
            app_names = ["admin", "auth", "tasks", "annotation"]
        for name in app_names:
            try:
                __import__(f"app.{name}", fromlist=["__init__"])
                logger.debug(f"AppRegistry: discovered app '{name}'")
            except ImportError as e:
                logger.warning(f"AppRegistry: app '{name}' not found, skipped: {e}")

    @classmethod
    def clear(cls) -> None:
        """清空注册 (主要用于测试)"""
        cls._apps.clear()
        cls._initialized = False

    @classmethod
    def summary(cls) -> Dict[str, str]:
        """获取注册摘要 (调试用)"""
        return {name: f"v{app.version}" for name, app in cls._apps.items()}


__all__ = ["AppRegistry"]
