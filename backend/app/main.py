"""
FastAPI Application Entry (v3.0.0 Stage 2.7 自动挂载)
======================================================

**v3.0.0 Stage 2.7 重构**:
- 路由注册从手写 14 行 `app.include_router()` 改为 `AppRegistry.iter_routes()` 自动挂载
- lifespan 启动/关闭调用 `AppRegistry.startup_all()` / `shutdown_all()`
- 业务应用 (admin / auth / tasks / annotation) 各自声明自己的路由条目, 0 硬编码

**使用方式**:
- 新增业务应用: 在 `app/{name}/__init__.py` 实现 `AppInterface` + `get_routes()`,
  main.py 0 修改即可自动挂载.
- 调整路由 prefix: 修改对应应用的 `get_routes()` 返回值, 无需改 main.py.

**保留的手动启动**:
- 跨应用基础设施: CORS / 异常处理 / 数据库 init / 资源释放 / ultralytics 配置
  (这些不属于单个应用, 而属于应用组合)
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import AppException, to_response_payload
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
    # v2.5.29: ultralytics 路径集中配置 (与 worker 启动时一致)
    # 防止 API 进程第一次调用 YOLO(...) 时把 .pt 落到 cwd
    with startup_profiler.step("ultralytics_setup"):
        try:
            from app.core.ultralytics_setup import configure_ultralytics, migrate_legacy_yolo_weights
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
        from app.core.redis_client import redis_client  # noqa: PLC0415
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

# 跨域配置（v2.5.15 P1-3: 含生产校验 + 互斥自动降级）
_cors_origins = settings.CORS_ORIGINS_LIST
_cors_allow_credentials = settings.CORS_ALLOW_CREDENTIALS

# 生产环境: '*' 是危险配置, 启动直接抛错
if settings.APP_ENV == "production" and "*" in _cors_origins:
    raise RuntimeError(
        "[CORS] CORS_ORIGINS cannot be '*' in production. "
        "Please set explicit origins via CORS_ORIGINS env var "
        "(comma-separated, e.g. 'https://app.example.com,https://admin.example.com')."
    )

# 浏览器规范: '*' + credentials=True 互斥, 开发环境自动降级
if "*" in _cors_origins and _cors_allow_credentials:
    import warnings
    warnings.warn(
        "[CORS] '*' origin with credentials=True is invalid per CORS spec. "
        "Auto-downgrading credentials to False. "
        "Set explicit origins to use credentials.",
        stacklevel=2,
    )
    _cors_allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stage 3: Request ID 中间件 (在所有其他中间件之前, 让后续中间件都能读到 rid)
from app.middleware.http import RequestIDMiddleware  # noqa: E402
app.add_middleware(RequestIDMiddleware)

# Stage 3: Request Timing 中间件 (在 RequestID 之后, 业务之前)
from app.middleware.http import RequestTimingMiddleware  # noqa: E402
app.add_middleware(RequestTimingMiddleware)

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
#  全局异常处理 (统一响应格式 + 防止内部堆栈泄漏)
# ============================================================
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """业务异常: 转换为统一 {code, message} 响应"""
    logging.getLogger("app.main").warning(
        "AppException %s on %s %s: %s",
        exc.code, request.method, request.url.path, exc.message,
    )
    return JSONResponse(status_code=exc.status_code, content=to_response_payload(exc))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常: 记录完整堆栈, 对前端仅返回脱敏的 500"""
    logging.getLogger("app.main").exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": "内部错误，请联系管理员"},
    )


# ============================================================
#  Console-script entry
# ============================================================
#  pyproject.toml 中声明的 console_script：
#    image-annotation-backend = "app.main:run"
#  安装后可直接调用 `image-annotation-backend`，等价于 `python run.py`。
def run() -> None:
    """Console-script entry: `image-annotation-backend`"""
    from app.cli import main
    raise SystemExit(main())
