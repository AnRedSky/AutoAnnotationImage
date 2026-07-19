"""
FastAPI Application Entry
=========================
基于深度学习的图像分类自动标注与人工修正系统
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.api import (
    auth, user, dataset, image, annotation,
    training, model as model_api, files,
    auto_annotate, export, stats, system,
    detection,  # v2.0.0 目标检测
    segmentation,  # v2.0.0 图像分割
)
from app.config import settings
from app.database import init_db

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
    # 启动
    await init_db()
    yield
    # 关闭（可清理资源）


app = FastAPI(
    title="Image Annotation System API",
    description="基于深度学习的图像分类自动标注与人工修正系统",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# 跨域配置（从 CORS_ORIGINS 读取）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS_LIST,
    allow_credentials=True,
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
#  Console-script entry
# ============================================================
#  pyproject.toml 中声明的 console_script：
#    image-annotation-backend = "app.main:run"
#  安装后可直接调用 `image-annotation-backend`，等价于 `python run.py`。
def run() -> None:
    """Console-script entry: `image-annotation-backend`"""
    from app.cli import main
    raise SystemExit(main())
