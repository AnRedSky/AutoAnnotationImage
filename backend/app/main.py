"""
FastAPI Application Entry (v3.0.0 Stage 2.7 自动挂载 + Stage 5.2 中间件统一注册)
==============================================================================

**v3.0.0 Stage 2.7 重构**:
- 路由注册从手写 14 行 `app.include_router()` 改为 `AppRegistry.iter_routes()` 自动挂载
- lifespan 启动/关闭调用 `AppRegistry.startup_all()` / `shutdown_all()`
- 业务应用 (admin / auth / tasks / annotation) 各自声明自己的路由条目, 0 硬编码

**v3.0.0 Stage 5.2 重构**:
- 中间件注册从内联 4 段 (CORS / RequestID / RequestTiming / 异常处理) 改为
  `MiddlewareRegistry.discover()` + `MiddlewareRegistry.apply(app)` 一行调用
- 中间件按 `order` 升序注册 (CORS 10 → error_handler 20 → request_id 30 → request_timing 40)
- 跨应用基础设施: 数据库 init / 资源释放 / ultralytics 配置
  (这些不属于单个应用, 而属于应用组合)

**使用方式**:
- 新增业务应用: 在 `app/{name}/__init__.py` 实现 `AppInterface` + `get_routes()`,
  main.py 0 修改即可自动挂载.
- 调整路由 prefix: 修改对应应用的 `get_routes()` 返回值, 无需改 main.py.
- 新增中间件: 在 `app/middleware/http/` 添加 xxx_factory(app) 函数, 在
  `app/middleware/http/__init__.py` 注册 `MiddlewareEntry`, main.py 0 修改即可生效.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.database import init_db, engine
from app.registry import AppRegistry

logger = logging.getLogger("app.main")

# ---- 在最早期强制禁用 HF symlink (Windows [WinError 14007] 根因) ----
# config.py 已经把 env 写入了 os.environ, 但 huggingface_hub 内部有时会缓存
# 常量. 这里在 import 业务模块 / timm 之前再显式 set 一次兜底。
try:
    import os as _os
    import huggingface_hub.constants as _hf_const
    _hf_const.HF_HUB_DISABLE_SYMLINKS = bool(settings.HF_HUB_DISABLE_SYMLINKS)
    _hf_const.HF_HUB_DISABLE_SYMLINKS_WARNING = bool(settings.HF_HUB_DISABLE_SYMLINKS_WARNING)
    # 同步镜像端点 (确保后续 timm.create_model 走国内镜像)
    if settings.HF_ENDPOINT:
        _hf_const.HF_ENDPOINT = settings.HF_ENDPOINT
        _hf_const.HUGGINGFACE_HUB_ENDPOINT = settings.HUGGINGFACE_HUB_ENDPOINT
        _os.environ.setdefault("HF_ENDPOINT", settings.HF_ENDPOINT)
        _os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", settings.HUGGINGFACE_HUB_ENDPOINT)
except Exception:
    # huggingface_hub 暂未安装也不影响 FastAPI 启动
    pass


# ---- Stage 2.7: 自动发现 + 注册 4 个业务应用 ----
# 触发 app/{admin,auth,tasks,annotation}/__init__.py 的 AppRegistry.register(...)
# 调用, 之后 iter_routes() 即可拿到全部 14 个 RouteEntry.
AppRegistry.discover_apps(["admin", "auth", "tasks", "annotation"])

# ---- Stage 4: 自动发现 + 注册插件 ----
# 触发 plugin/{storage_backends,ml_backends,task_queues,notification_channels}/__init__.py 的
# PluginRegistry.register(...) 调用. 业务代码可通过 PluginRegistry.get_default(...) 获取.
from app.registry import PluginRegistry as _PR  # noqa: E402
_PR.discover_plugins(["storage_backends", "ml_backends", "task_queues", "notification_channels"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动/关闭时执行

    **Stage 2.7 变更**:
    - 启动时调用 `AppRegistry.startup_all()` 让每个应用执行自己的 startup 钩子
    - 关闭时调用 `AppRegistry.shutdown_all()` 让每个应用执行自己的 shutdown 钩子
    - 保留: 数据库 init / 资源释放 / ultralytics 配置 (这些是跨应用基础设施)
    """
    # ---- 启动 ----
    # Stage 5.1: 启动耗时分析
    from app.core.startup_profiler import startup_profiler
    startup_profiler.begin()
    with startup_profiler.step("init_db"):
        await init_db()
    # v3.6.0+: 按规范化编号升序执行 migrations/ 下的迁移脚本
    # (00 -> 01 -> ... -> 10, 全部幂等, 失败立即停止)
    with startup_profiler.step("ordered_migrations"):
        try:
            from migrations.runner import run_all_migrations
            results = await run_all_migrations()
            failed = [r for r in results if not r.success]
            if failed:
                logger.error(
                    "[startup] 迁移失败 %d 条, 拒绝启动以保护数据一致性: %s",
                    len(failed),
                    [(r.order, r.error) for r in failed],
                )
                raise RuntimeError(
                    f"ordered migrations failed: {[r.order for r in failed]}"
                )
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("[startup] 迁移执行器异常: %s", e)
            raise
    # v2.5.29: ultralytics 路径集中配置 (与 worker 启动时一致)
    # 防止 API 进程第一次调用 YOLO(...) 时把 .pt 落到 cwd
    with startup_profiler.step("ultralytics_setup"):
        try:
            from app.tasks.ml.ultralytics_setup import configure_ultralytics, migrate_legacy_yolo_weights
            configure_ultralytics()
            migrate_legacy_yolo_weights()
        except Exception as e:
            logger.warning(f"ultralytics_setup 失败, 不影响 API 启动: {e}")
    # 调用各应用的 startup 钩子 (Stage 2.7)
    with startup_profiler.step("app_startup"):
        try:
            await AppRegistry.startup_all()
        except Exception:
            logger.exception("AppRegistry.startup_all failed")
            raise
    # 调用各插件的 install 钩子 (Stage 4)
    with startup_profiler.step("plugin_install"):
        try:
            from app.registry import PluginRegistry as _PR_install  # noqa: PLC0415
            _PR_install.install_all()
        except Exception:
            logger.exception("PluginRegistry.install_all failed (non-fatal)")
    # 启动完成后输出报告
    startup_profiler.report()
    logger.info("Application started")
    yield
    # ---- 关闭 ----
    # 调用各应用的 shutdown 钩子 (Stage 2.7)
    try:
        await AppRegistry.shutdown_all()
    except Exception:  # noqa: BLE001
        logger.exception("AppRegistry.shutdown_all failed")
    # 调用各插件的 uninstall 钩子 (Stage 4)
    try:
        from app.registry import PluginRegistry as _PR_uninstall  # noqa: PLC0415
        _PR_uninstall.uninstall_all()
    except Exception:  # noqa: BLE001
        logger.exception("PluginRegistry.uninstall_all failed (non-fatal)")
    # 优雅释放数据库连接池与 Redis 连接，避免热重启丢数据/泄漏连接
    logger.info("Shutting down, disposing resources...")
    try:
        await engine.dispose()
    except Exception:  # noqa: BLE001
        logger.exception("engine.dispose() failed")
    try:
        from app.database.redis import redis_client  # noqa: PLC0415
        redis_client.close()
    except Exception:  # noqa: BLE001
        logger.exception("redis_client.close() failed")
    logger.info("Cleanup complete")


app = FastAPI(
    title="Image Annotation System API",
    description="基于深度学习的图像分类自动标注与人工修正系统",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# ============================================================
#  Stage 5.2: 中间件统一注册 (替代原内联 CORS / RequestID / Timing / 异常处理)
# ============================================================
# - MiddlewareRegistry.discover() 触发 app.middleware.http.__init__ 导入,
#   进而注册 4 个中间件条目 (cors / error_handler / request_id / request_timing)
# - MiddlewareRegistry.apply(app) 按 order 升序调用各 factory(app),
#   完成 add_middleware / add_exception_handler
# - 顺序约定: 10 (CORS) → 20 (error_handler) → 30 (request_id) → 40 (request_timing)
from app.registry import MiddlewareRegistry  # noqa: E402

MiddlewareRegistry.discover()
_mw_count = MiddlewareRegistry.apply(app)
logger.info(f"Total {_mw_count} middlewares applied via MiddlewareRegistry.apply()")

# ============================================================
#  路由自动挂载 (Stage 2.7)
# ============================================================
# 替代原来的 14 行手写 include_router:
#   app.include_router(auth.router, prefix="/api/auth", tags=["用户认证"])
#   app.include_router(user.router, prefix="/api/users", tags=["用户管理"])
#   ...
# 现在由 AppRegistry.iter_routes() 自动产出 14 个 RouteEntry,
# 每个应用通过 AppInterface.get_routes() 声明自己的 prefix + tags.
_mounted: list[tuple[str, str, list[str]]] = []
for app_name, entry in AppRegistry.iter_routes():
    app.include_router(
        entry.router,
        prefix=entry.prefix,
        tags=entry.tags or None,
    )
    _mounted.append((app_name, entry.prefix, entry.tags or []))
    logger.info(
        f"Auto-mounted: app={app_name} prefix={entry.prefix!r} "
        f"tags={entry.tags or []} routes={len(entry.router.routes)}"
    )
logger.info(f"Total {len(_mounted)} route groups mounted via AppRegistry.iter_routes()")


@app.get("/")
async def root():
    return {
        "name": "Image Annotation System",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


# 注：详细健康检查见 /api/health（含数据库/Redis/MinIO 状态）


# ============================================================
#  Console-script entry
# ============================================================
#  pyproject.toml 中声明的 console_script：
#    image-annotation-backend = "app.main:run"
#  安装后可直接调用 `image-annotation-backend`，等价于 `python run.py`。
def run() -> None:
    """Console-script entry: `image-annotation-backend`"""
    from app.core.cli import main
    raise SystemExit(main())
