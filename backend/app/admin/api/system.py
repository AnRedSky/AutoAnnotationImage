"""
System API: Health / Info (app/admin/api/)
========================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/system.py 迁入 admin 应用
(system 端点属于系统级管理面, 归属 admin 应用)
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """系统健康检查 (无需鉴权)"""
    import platform
    import time

    result = {
        "status": "ok",
        "timestamp": int(time.time()),
        "python": platform.python_version(),
        "app_env": settings.APP_ENV,
        "app_version": "1.0.0",
        "checks": {},
    }

    # 数据库
    try:
        t0 = time.time()
        await db.execute(text("SELECT 1"))
        result["checks"]["database"] = {
            "ok": True,
            "latency_ms": round((time.time() - t0) * 1000, 2),
        }
    except Exception as e:
        result["status"] = "degraded"
        result["checks"]["database"] = {"ok": False, "err": str(e)[:200]}

    # Redis
    try:
        import redis.asyncio as redis_async
        r = redis_async.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            db=settings.REDIS_DB,
            socket_connect_timeout=1.0,
        )
        t0 = time.time()
        await r.ping()
        result["checks"]["redis"] = {
            "ok": True,
            "latency_ms": round((time.time() - t0) * 1000, 2),
        }
        await r.aclose()
    except Exception as e:
        result["checks"]["redis"] = {"ok": False, "err": str(e)[:200]}

    # MinIO
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as client:
            t0 = time.time()
            r = await client.get(
                f"{'https' if settings.MINIO_SECURE else 'http'}://{settings.MINIO_ENDPOINT}/minio/health/live"
            )
            result["checks"]["minio"] = {
                "ok": r.status_code == 200,
                "latency_ms": round((time.time() - t0) * 1000, 2),
            }
    except Exception as e:
        result["checks"]["minio"] = {"ok": False, "err": str(e)[:200]}

    return result


@router.get("/system/info")
async def system_info():
    """系统信息 (无需鉴权)"""
    import platform
    import torch
    import timm
    return {
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "env": settings.APP_ENV,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "timm": timm.__version__,
        "default_model": settings.EFFECTIVE_BASE_MODEL,
        "inference_device": settings.INFERENCE_DEVICE,
        "upload_dir": str(settings.UPLOAD_DIR),
        "model_dir": str(settings.MODEL_DIR),
    }
