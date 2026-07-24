"""
CORS 中间件配置 (Middleware Layer)
==================================

v3.0.0 新增 (Phase 1.10): 从 app.main 内联提取
"""
import warnings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings


def setup_cors(app: FastAPI) -> None:
    """注册 CORS 中间件

    - 生产环境: CORS_ORIGINS='*' 直接抛错 (启动失败)
    - 开发环境: '*' + credentials=True 互斥时自动降级 credentials=False
    """
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
