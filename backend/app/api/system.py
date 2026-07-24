"""
System API: Health / Info
=========================
为运维 / 前端 / 业务方提供:
  - GET /api/health        服务健康检查
  - GET /api/system/info   系统信息（模型列表 / 配置 / 版本）
"""
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.database import get_db
from app.config import settings

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    系统健康检查（无需鉴权, 给 K8s liveness 探针 / 前端首页 / 监控用）
    返回各项依赖状态
    """
    import platform
    import sys
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

    # Redis（Celery broker / 后端用）
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
        # Redis 不可用不算 fatal, Celery 会降级

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
    """
    系统信息（无需鉴权）: 前端 about 页面 / 运维巡检
    """
    import platform
    import sys
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
