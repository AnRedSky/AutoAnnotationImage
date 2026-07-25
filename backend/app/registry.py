"""
应用注册中心 (App Layer)
========================

**职责**:
- 注册所有业务应用 (AppInterface 实现类)
- 提供应用路由的自动聚合 (main.py 用 iter_routes() 挂载)
- 提供应用启动/关闭钩子的统一调用 (lifespan 用 startup_all / shutdown_all)
- 提供按名查询单个应用 (e.g. AppRegistry.get("tasks"))
- 注册所有插件 (PluginInterface 实现类) — Stage 4 新增
- 提供插件的分类索引 (PluginRegistry.list_by_category) — Stage 4 新增
- 注册所有中间件 (MiddlewareEntry) — Stage 5.2 新增
- 提供中间件的统一应用 (MiddlewareRegistry.apply) — Stage 5.2 新增

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

# 在 plugin/storage_backends/local.py
from app.registry import PluginRegistry
class LocalStoragePlugin(PluginInterface):
    name = "local_storage"
    version = "1.0.0"
    category = "storage"
    def install(self): ...
    def uninstall(self): ...

PluginRegistry.register(LocalStoragePlugin())

# 在 app/middleware/http/__init__.py (Stage 5.2)
from app.registry import MiddlewareRegistry
from app.common.interfaces import MiddlewareEntry
from app.middleware.http.cors import cors_factory

MiddlewareRegistry.register(MiddlewareEntry(
    name="cors", factory=cors_factory, order=10, description="CORS 跨域配置"
))
```

**自动发现**:
main.py 调用 `AppRegistry.discover_apps(["admin", "auth", "tasks", "annotation"])`,
会 import app/{name}/__init__.py 触发 register() 调用, 避免手写 import 列表.
同理 `PluginRegistry.discover_plugins(["storage_backends", ...])` 自动发现插件.
`MiddlewareRegistry.discover()` 自动发现 `app.middleware.http` 下的中间件.

**路由挂载** (Stage 2.7):
main.py 用 `for entry in AppRegistry.iter_routes(): app.include_router(entry.router, prefix=entry.prefix, tags=entry.tags)`,
无需手写 14 行 include_router.

**中间件挂载** (Stage 5.2):
main.py 用 `MiddlewareRegistry.apply(app)`, 按 order 升序应用, 无需在 main.py 内联.

v3.0.0 Stage 2 新增
v3.0.0 Stage 2.7: 新增 iter_routes() 辅助方法
v3.0.0 Stage 4: 新增 PluginRegistry
v3.0.0 Stage 5.2: 新增 MiddlewareRegistry
"""
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from app.common.interfaces import (
        AppInterface,
        MiddlewareEntry,
        PluginInterface,
        RouteEntry,
    )
    from fastapi import FastAPI

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


class PluginRegistry:
    """插件注册中心 (单例, Stage 4 新增)

    与 AppRegistry 类似, 但索引按 (category, name) 双重键.
    用于管理可插拔的能力后端 (存储/ML/任务队列/通知渠道).

    业务代码通过 `PluginRegistry.get(category, name)` 获取插件实例,
    或通过 `PluginRegistry.get_default(category)` 获取分类下的默认插件.

    示例:
        >>> storage = PluginRegistry.get_default("storage")  # 默认 LocalStorage
        >>> s3_storage = PluginRegistry.get("storage", "s3")  # 显式指定 S3
    """

    _plugins: Dict[str, Dict[str, "PluginInterface"]] = {}  # category -> {name -> plugin}
    _defaults: Dict[str, str] = {}  # category -> default_name

    @classmethod
    def register(
        cls,
        plugin: "PluginInterface",
        *,
        make_default: bool = False,
    ) -> None:
        """注册一个插件

        Args:
            plugin: 实现 PluginInterface 的实例
            make_default: 是否设为该 category 的默认插件 (仅当当前无默认)
        """
        cat = plugin.category
        if cat not in cls._plugins:
            cls._plugins[cat] = {}
        if plugin.name in cls._plugins[cat]:
            logger.warning(
                f"PluginRegistry: plugin '{cat}/{plugin.name}' already registered, overwriting"
            )
        cls._plugins[cat][plugin.name] = plugin
        logger.info(
            f"PluginRegistry: registered plugin '{cat}/{plugin.name}' v{plugin.version}"
        )

        # 默认插件
        if make_default and cat not in cls._defaults:
            cls._defaults[cat] = plugin.name
        elif cat not in cls._defaults:
            # 第一个注册的插件自动成为默认
            cls._defaults[cat] = plugin.name

    @classmethod
    def set_default(cls, category: str, name: str) -> None:
        """设置分类的默认插件"""
        if category not in cls._plugins:
            raise KeyError(f"PluginRegistry: category '{category}' has no plugins")
        if name not in cls._plugins[category]:
            raise KeyError(
                f"PluginRegistry: plugin '{category}/{name}' not registered"
            )
        cls._defaults[category] = name
        logger.info(f"PluginRegistry: default for '{category}' set to '{name}'")

    @classmethod
    def get(cls, category: str, name: Optional[str] = None) -> Optional["PluginInterface"]:
        """获取插件实例

        Args:
            category: 分类 (storage / ml_backend / task_queue / notification)
            name: 插件名, None = 该分类的默认插件

        Returns:
            插件实例, 不存在则 None
        """
        if category not in cls._plugins:
            return None
        if name is None:
            name = cls._defaults.get(category)
            if name is None:
                return None
        return cls._plugins[category].get(name)

    @classmethod
    def get_default(cls, category: str) -> Optional["PluginInterface"]:
        """获取分类的默认插件 (便捷方法)"""
        return cls.get(category)

    @classmethod
    def list_by_category(cls, category: str) -> List["PluginInterface"]:
        """列出分类下所有已注册插件"""
        if category not in cls._plugins:
            return []
        return list(cls._plugins[category].values())

    @classmethod
    def all_categories(cls) -> List[str]:
        """获取所有已注册分类"""
        return list(cls._plugins.keys())

    @classmethod
    def summary(cls) -> Dict[str, Dict[str, str]]:
        """获取注册摘要 (调试用)

        Returns:
            {category: {name: version}, ...}
        """
        return {
            cat: {p.name: p.version for p in plugins.values()}
            for cat, plugins in cls._plugins.items()
        }

    @classmethod
    def clear(cls) -> None:
        """清空注册 (主要用于测试)"""
        cls._plugins.clear()
        cls._defaults.clear()

    @classmethod
    def install_all(cls) -> int:
        """对所有已注册插件调用 install() 钩子 (Stage 4 新增)

        main.py 在 lifespan startup 中调用, 触发每个插件的 install() 钩子
        (e.g. LocalStoragePlugin 验证存储路径, SSENotificationPlugin 验证 Redis 连接).

        Returns:
            成功 install 的插件数量
        """
        ok_count = 0
        for cat, plugins in cls._plugins.items():
            for pname, plugin in plugins.items():
                try:
                    plugin.install()
                    ok_count += 1
                    logger.debug(f"PluginRegistry: installed {cat}/{pname}")
                except Exception as e:  # noqa: BLE001
                    logger.exception(
                        f"PluginRegistry: install failed for {cat}/{pname}: {e!r}"
                    )
        logger.info(f"PluginRegistry: install_all completed ({ok_count} plugins)")
        return ok_count

    @classmethod
    def uninstall_all(cls) -> int:
        """对所有已注册插件调用 uninstall() 钩子 (Stage 4 新增)

        main.py 在 lifespan shutdown 中调用, 触发每个插件的清理逻辑.

        Returns:
            成功 uninstall 的插件数量
        """
        ok_count = 0
        for cat, plugins in cls._plugins.items():
            for pname, plugin in plugins.items():
                try:
                    plugin.uninstall()
                    ok_count += 1
                except Exception as e:  # noqa: BLE001
                    logger.exception(
                        f"PluginRegistry: uninstall failed for {cat}/{pname}: {e!r}"
                    )
        logger.info(f"PluginRegistry: uninstall_all completed ({ok_count} plugins)")
        return ok_count

    @classmethod
    def discover_plugins(cls, category_names: Optional[List[str]] = None) -> None:
        """自动发现并导入插件 (触发 register 调用)

        Args:
            category_names: 要发现的分类目录列表 (None = 发现所有标准分类)
                例如 ["storage_backends", "ml_backends", "task_queues", "notification_channels"]
        """
        if category_names is None:
            category_names = [
                "storage_backends",
                "ml_backends",
                "task_queues",
                "notification_channels",
            ]
        for cat in category_names:
            try:
                __import__(f"plugin.{cat}", fromlist=["__init__"])
                logger.debug(f"PluginRegistry: discovered category '{cat}'")
            except ImportError as e:
                logger.warning(f"PluginRegistry: category '{cat}' not found, skipped: {e}")


class MiddlewareRegistry:
    """中间件注册中心 (单例) — Stage 5.2 新增

    设计目标:
    - 让 main.py 不再关心中间件的具体实现与加载顺序
    - 中间件模块通过 import-time register() 自我声明
    - 通过 order 字段声明加载顺序 (升序 = 从内到外), 避免 main.py 靠位置控制

    顺序约定:
    Starlette/FastAPI 中, 后 add_middleware 的中间件处于更外层. 因此我们按
    `order` 升序依次 `factory(app)`, 先 add 的在内层, 后 add 的在外层,
    恰好让 `order` 升序 = 从内到外.

    推荐 order 区间:
    - 10: CORS (最内层, 路由直接看到 CORS header)
    - 20: 异常处理 (确保异常日志附带 rid)
    - 30: RequestID (让外层中间件能读到 rid)
    - 40: RequestTiming (最外层, 记录整体耗时)

    使用方式:
    ```python
    # app/middleware/http/__init__.py
    from app.registry import MiddlewareRegistry
    from app.common.interfaces import MiddlewareEntry
    from app.middleware.http.cors import cors_factory
    from app.middleware.http.error_handler import error_handler_factory
    from app.middleware.http.request_id import request_id_factory
    from app.middleware.http.request_timing import request_timing_factory

    for entry in (
        MiddlewareEntry("cors", cors_factory, order=10, description="CORS"),
        MiddlewareEntry("error_handler", error_handler_factory, order=20, description="异常处理"),
        MiddlewareEntry("request_id", request_id_factory, order=30, description="RequestID"),
        MiddlewareEntry("request_timing", request_timing_factory, order=40, description="RequestTiming"),
    ):
        MiddlewareRegistry.register(entry)
    ```

    ```python
    # app/main.py
    from app.registry import MiddlewareRegistry
    MiddlewareRegistry.discover()
    MiddlewareRegistry.apply(app)
    ```
    """

    _entries: List["MiddlewareEntry"] = []

    @classmethod
    def register(cls, entry: "MiddlewareEntry") -> None:
        """注册一个中间件条目

        重复 name 注册: warning 后跳过 (避免 FastAPI 抛 Cannot add middleware).

        Args:
            entry: MiddlewareEntry 实例
        """
        if any(e.name == entry.name for e in cls._entries):
            logger.warning(
                f"MiddlewareRegistry: middleware '{entry.name}' already registered, skipping"
            )
            return
        cls._entries.append(entry)
        logger.info(
            f"MiddlewareRegistry: registered middleware '{entry.name}' "
            f"(order={entry.order}, desc={entry.description!r})"
        )

    @classmethod
    def get(cls, name: str) -> Optional["MiddlewareEntry"]:
        """按名获取中间件条目 (调试用)"""
        for entry in cls._entries:
            if entry.name == name:
                return entry
        return None

    @classmethod
    def all(cls) -> List["MiddlewareEntry"]:
        """返回按 order 升序排列的条目列表 (调试用)

        多次 register 不会重复, 内部用 append 不会因相同 order 而覆盖.
        """
        return sorted(cls._entries, key=lambda e: e.order)

    @classmethod
    def apply(cls, app: "FastAPI") -> int:
        """按 order 升序对所有条目调用 factory(app).

        失败隔离: 单个 factory 抛异常不会影响其他中间件的注册 (try/except 包裹).
        这样设计是因为某个中间件 (e.g. CORS 配置错误) 不应该阻塞其他中间件 (e.g. RequestID).

        Returns:
            成功 apply 的中间件数量
        """
        ok_count = 0
        for entry in cls.all():
            try:
                entry.factory(app)
                ok_count += 1
                logger.debug(f"MiddlewareRegistry: applied '{entry.name}' (order={entry.order})")
            except Exception:  # noqa: BLE001
                logger.exception(
                    f"MiddlewareRegistry: failed to apply '{entry.name}' (order={entry.order})"
                )
        logger.info(f"MiddlewareRegistry: apply completed ({ok_count}/{len(cls._entries)} middlewares)")
        return ok_count

    @classmethod
    def clear(cls) -> None:
        """清空注册 (主要用于测试)"""
        cls._entries.clear()

    @classmethod
    def discover(cls) -> None:
        """自动发现并导入中间件 (触发 register 调用)

        与 AppRegistry.discover_apps / PluginRegistry.discover_plugins 一致:
        通过 __import__ 触发 `app.middleware.http.__init__` 导入, 进而触发
        其中对 MiddlewareRegistry.register(...) 的调用.

        注意: 因为 `app.middleware.http.auth` 包含 FastAPI Depends 依赖 (不是
        Starlette 中间件), 我们只 import http 子包, 让其中的 `__init__.py` 决定
        注册哪些 middleware. auth 模块本身不应出现在 MiddlewareRegistry 中.
        """
        try:
            __import__("app.middleware.http", fromlist=["__init__"])
            logger.debug("MiddlewareRegistry: discovered 'app.middleware.http'")
        except ImportError as e:
            logger.warning(f"MiddlewareRegistry: 'app.middleware.http' not found, skipped: {e}")

    @classmethod
    def summary(cls) -> Dict[str, Dict[str, Any]]:
        """获取注册摘要 (调试用)

        Returns:
            {name: {order, description}, ...}
        """
        return {
            e.name: {"order": e.order, "description": e.description}
            for e in cls.all()
        }


__all__ = ["AppRegistry", "PluginRegistry", "MiddlewareRegistry"]
