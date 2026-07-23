"""
FastAPI Application Entry
=========================
基于深度学习的图像分类自动标注与人工修正系统
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from app.api import (
    auth, user, dataset, image, annotation,
    training, model as model_api, files,
    auto_annotate, export, stats, system,
    detection,  # v2.0.0 目标检测
    segmentation,  # v2.0.0 图像分割
)
from app.config import settings
from app.database import init_db, engine
from app.core.exceptions import AppException, to_response_payload

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动/关闭时执行"""
    import logging  # noqa: PLC0415
    logger = logging.getLogger("app.main")
    # 启动
    await init_db()
    logger.info("Application started")
    yield
    # 关闭: 优雅释放数据库连接池与 Redis 连接，避免热重启丢数据/泄漏连接
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

# 路由注册
app.include_router(auth.router, prefix="/api/auth", tags=["用户认证"])
app.include_router(user.router, prefix="/api/users", tags=["用户管理"])
app.include_router(dataset.router, prefix="/api/datasets", tags=["数据集管理"])
app.include_router(image.router, prefix="/api/images", tags=["图像管理"])
app.include_router(annotation.router, prefix="/api/annotations", tags=["标注管理"])
app.include_router(auto_annotate.router, prefix="/api/auto-annotate", tags=["AI预标注"])
app.include_router(training.router, prefix="/api/training", tags=["模型训练"])
app.include_router(model_api.router, prefix="/api/models", tags=["模型管理"])
app.include_router(export.router, prefix="/api/export", tags=["标注导出"])
app.include_router(stats.router, prefix="/api/stats", tags=["统计分析"])
app.include_router(files.router, prefix="/api/files", tags=["文件服务"])
# v2.0.0 目标检测: bbox 标注 CRUD + 训练 (S3+ 训练) 端点
app.include_router(detection.router, prefix="/api/detection", tags=["目标检测"])
# v2.0.0 图像分割: mask CRUD (S5)
app.include_router(segmentation.router, prefix="/api/segmentation", tags=["图像分割"])
# system router 暴露 /api/health, /api/system/info 两个无鉴权端点
app.include_router(system.router, prefix="/api", tags=["系统"])


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
    import logging  # noqa: PLC0415
    logging.getLogger("app.main").warning(
        "AppException %s on %s %s: %s",
        exc.code, request.method, request.url.path, exc.message,
    )
    return JSONResponse(status_code=exc.status_code, content=to_response_payload(exc))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常: 记录完整堆栈, 对前端仅返回脱敏的 500"""
    import logging  # noqa: PLC0415
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
