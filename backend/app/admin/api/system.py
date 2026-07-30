"""
System API: Health / Info / Metrics (app/admin/api/)
====================================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/system.py 迁入 admin 应用
(system 端点属于系统级管理面, 归属 admin 应用)

**v3.0.0 Stage 5.3 扩展**: 新增 /api/metrics 监控聚合端点
- 启动耗时 (StartupProfiler)
- 缓存 hit/miss 统计
- 慢 SQL 监控 (前 10 条 + 累计计数)
- 注册中心快照 (AppRegistry + PluginRegistry)
- 进程级资源 (CPU/内存/线程数)
"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database import get_db

router = APIRouter()


@router.get("/health")
async def health_check(response: Response, db: AsyncSession = Depends(get_db)):
    """系统健康检查 (无需鉴权).

    返回状态契约:
    - 数据库不可用 → **HTTP 503** (K8s readiness probe / 阿里云 SLB 才会停止路由)
    - Redis 不可用 → HTTP 200 + status=degraded (业务有 cache fallback)
    - MinIO 不可用 → HTTP 200 + status=degraded (导出端点会受影响, 但 API 元服务还在)
    - 全部可用 → HTTP 200 + status=ok

    设计动机 (v3.1.0 Phase T, 落档 P0-2):
    老版本一律返 200, 让 LB 持续把流量路由到 DB 已挂的实例.
    """
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
        result["status"] = "unhealthy"  # 比 "degraded" 更准确地描述 DB 挂
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
        if result["status"] == "ok":  # DB 健康时, redis 挂 = degraded
            result["status"] = "degraded"
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
        if result["status"] == "ok":
            result["status"] = "degraded"
        result["checks"]["minio"] = {"ok": False, "err": str(e)[:200]}

    # 状态码契约: DB 挂 → 503, 其余 degraded → 200
    if result["status"] == "unhealthy":
        # 这种情形健康端点必须 503, 否则 K8s readiness probe / LB 持续路由到死实例
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content=result)

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


# ============== Stage 5.3: 监控聚合端点 ==============

@router.get("/metrics")
async def metrics():
    """监控聚合端点 (无需鉴权, 但建议生产环境加白名单)

    返回 4 大维度:
    - apps/plugins: 注册中心快照
    - cache: 缓存 hit/miss 统计
    - sql: 慢 SQL 监控 (前 10 + 累计)
    - process: 进程级资源 (CPU/内存/线程)

    设计: 全部为只读操作, 单次响应 < 100ms
    """
    import os
    import platform
    import threading
    import time

    result: dict = {
        "ts": int(time.time()),
        "env": settings.APP_ENV,
    }

    # 1) 注册中心快照
    try:
        from app.registry import AppRegistry, PluginRegistry
        result["apps"] = {
            "count": len(AppRegistry.all_apps()),
            "summary": AppRegistry.summary(),
        }
        result["plugins"] = {
            "categories": PluginRegistry.all_categories(),
            "summary": PluginRegistry.summary(),
        }
    except Exception as e:  # noqa: BLE001
        result["registry_err"] = str(e)[:200]

    # 2) 缓存统计
    try:
        from app.common.cache import cache
        result["cache"] = cache.get_stats()
    except Exception as e:  # noqa: BLE001
        result["cache"] = {"err": str(e)[:200]}

    # 3) 慢 SQL 监控
    try:
        from app.database.slow_sql import get_stats
        result["sql"] = get_stats()
    except Exception as e:  # noqa: BLE001
        result["sql"] = {"err": str(e)[:200]}

    # 4) 进程级资源
    try:
        proc_status: dict = {}
        # 尝试 /proc/self/status (Linux/Mac)
        try:
            with open(f"/proc/{os.getpid()}/status") as f:
                for line in f:
                    if line.startswith(("VmRSS:", "VmSize:", "Threads:")):
                        key, val = line.strip().split(":", 1)
                        proc_status[key.strip()] = val.strip()
        except (FileNotFoundError, OSError):
            # Windows: 用 psutil (可选) 或退化为线程数
            proc_status["threads"] = str(threading.active_count())

        # 通用字段
        result["process"] = {
            "pid": os.getpid(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "threads_alive": threading.active_count(),
            "proc_status": proc_status,
        }
    except Exception as e:  # noqa: BLE001
        result["process"] = {"err": str(e)[:200]}

    # 5) 数据库连接池状态 (Stage 5.3 新增)
    try:
        from app.database import engine
        pool = engine.pool
        result["db_pool"] = {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception as e:  # noqa: BLE001
        result["db_pool"] = {"err": str(e)[:200]}

    return result
