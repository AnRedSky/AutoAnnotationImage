"""
segmentation.progress 模块 — 训练/自动标注进度查询
==================================================

**v3.0.0 Phase S2 拆分**: 从 segmentation.py 抽离
**职责**: TrainingJob 进度查询 + Celery task_id 进度查询 + SSE 实时推送

**路由清单** (3 个):
- GET /jobs/{job_id}/progress             老接口 (int job_id, 保留向后兼容)
- GET /progress/{task_id}                 v2.5.35 新增 (Celery UUID, REST 轮询)
- GET /progress/stream/{task_id}          v2.5.35 新增 (Celery UUID, SSE)

**S2 进度源约定**:
- TrainingJob 表 (训练任务走的是 _create_job 路径, celery_task_id 是主键索引列)
- Celery AsyncResult (auto_annotate_segmentation_task 不写 TrainingJob, 只走 update_state)
- DB 优先, 缺失时回退到 Celery, `source` 字段标识

**S2 SSE 约定**:
- 每 1.0s 轮询一次, 仅在 signature (state/progress/message) 变化时 yield
- 终态 (SUCCESS/FAILURE/REVOKED) 立即发 event: end 后断开
- request.is_disconnected() 监听客户端断开, 避免 zombie 连接
- 失败兜底: 异常时 yield {state: FAILURE, message: error} 后退出

**v3.3.0 P0 修复**: 必须鉴权 + 校验所有权, 防止跨用户 segmentation 进度泄露
"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.tasks.model.training_job import TrainingJob
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user, get_user_optional_for_query

router = APIRouter()


async def _assert_can_access_segmentation_task(task_id: str, current_user: User) -> None:
    """v3.3.0 P0: 校验用户对 segmentation 任务的访问权 (admin / owner)

    v3.3.4-PATCH 修复 (admin 旁路):
      - 旧逻辑: is_admin() 旁路
      - 新逻辑: 仅 super_admin 旁路, regular admin 走 owner 校验
    """
    if current_user.is_super_admin():
        return
    async with AsyncSessionLocal() as db:
        job = (await db.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalar_one_or_none()
    if job is None:
        raise HTTPException(403, "无权限访问此任务进度")
    if job.user_id != current_user.id:
        raise HTTPException(403, "无权限访问此任务进度")


# ============== 工具函数 ==============

async def _resolve_segmentation_task_progress(task_id: str) -> dict:
    """统一解析 Celery task_id -> 进度 dict, 同时覆盖训练 + 自动标注两种来源.

    优先级:
    1. TrainingJob 表 (训练任务走的是 _create_job 路径, celery_task_id 是主键索引列)
    2. Celery AsyncResult (auto_annotate_segmentation_task 不写 TrainingJob, 只走 update_state)
    """
    from celery.result import AsyncResult
    from app.database import AsyncSessionLocal

    db_row = None
    try:
        async with AsyncSessionLocal() as db:
            db_row = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
    except Exception:
        db_row = None

    celery_state = "PENDING"
    celery_info: dict = {}
    celery_result_payload = None
    try:
        result = AsyncResult(task_id)
        try:
            celery_state = result.state
        except Exception:
            celery_state = "PENDING"
        try:
            raw = result.info
            if isinstance(raw, dict):
                celery_info = raw
        except Exception:
            celery_info = {}
        try:
            if celery_state == "SUCCESS":
                celery_result_payload = result.result
        except Exception:
            celery_result_payload = None
    except Exception:
        pass

    if db_row is not None:
        state = db_row.state or celery_state
        progress = float(db_row.progress) if db_row.progress is not None else float(celery_info.get("progress", 0))
        message = db_row.message or celery_info.get("msg") or celery_info.get("info") or ""
        if db_row.error and not message:
            message = db_row.error[:200]
        return {
            "task_id": task_id,
            "state": state,
            "progress": round(progress, 2),
            "message": message,
            "current_epoch": getattr(db_row, "current_epoch", None),
            "total_epochs": db_row.epochs,
            "started_at": db_row.started_at.isoformat() if db_row.started_at else None,
            "finished_at": db_row.finished_at.isoformat() if db_row.finished_at else None,
            "duration_seconds": db_row.duration_seconds,
            "data_total": db_row.data_total,
            "data_train": db_row.data_train,
            "data_val": db_row.data_val,
            "num_classes": db_row.num_classes,
            "class_names": db_row.class_names,
            "model_version_id": db_row.model_version_id,
            "job_id": db_row.id,
            "source": "db",
        }

    return {
        "task_id": task_id,
        "state": celery_state,
        "progress": round(float(celery_info.get("progress", 0)), 2),
        "message": celery_info.get("msg") or celery_info.get("info") or "",
        "current_epoch": celery_info.get("current_epoch") or celery_info.get("epoch"),
        "total_epochs": celery_info.get("total_epochs"),
        "result": celery_result_payload if isinstance(celery_result_payload, dict) else None,
        "source": "celery" if celery_state != "PENDING" else "unknown",
    }


# ============== 老接口: TrainingJob id ==============

@router.get("/jobs/{job_id}/progress")
async def get_segmentation_job_progress(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """拉取分割 TrainingJob 进度 (轮询)

    v3.3.0 P0 修复: 必须校验所有权
    v3.3.4-PATCH: 收紧为仅 super_admin 旁路 (regular admin 仍受 owner 校验)
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, f"TrainingJob id={job_id} not found")
    if not current_user.is_super_admin() and job.user_id != current_user.id:
        raise HTTPException(403, "无权限查看此训练任务进度")
    return {
        "id": job.id,
        "celery_task_id": job.celery_task_id,
        "task_type": job.task_type,
        "state": job.state,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "current_epoch": getattr(job, "current_epoch", None),
        "duration_seconds": job.duration_seconds,
        "model_version_id": job.model_version_id,
    }


# ============== v2.5.35 新增: 与前端 segmentationApi 路径对齐的进度端点 ==============
# 前端 segmentationApi.progress 调 GET /api/segmentation/progress/{taskId} (Celery UUID).
# 老的 /jobs/{job_id}/progress 端点保留 (int TrainingJob id), 但前端轮询走新端点.

@router.get("/progress/{task_id}")
async def get_segmentation_progress(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    拉取 segmentation 任务进度 (REST 轮询)

    v2.5.35 新增: 与前端 segmentationApi.progress 路径对齐

    v3.3.0 P0 修复: 必须校验所有权
    """
    await _assert_can_access_segmentation_task(task_id, current_user)
    return await _resolve_segmentation_task_progress(task_id)


@router.get("/progress/stream/{task_id}")
async def stream_segmentation_progress(
    task_id: str,
    request: Request,
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_user_optional_for_query),
):
    """
    SSE 实时推送 segmentation 任务进度 (v2.5.35 新增)

    v3.3.0 P0 修复: 必须校验所有权, 防止跨用户 SSE 进度泄露
    """
    if current_user is None:
        raise HTTPException(401, "未授权: 需要有效的 access_token (query ?token= 或 Authorization header)")
    await _assert_can_access_segmentation_task(task_id, current_user)

    SSE_SEG_POLL_INTERVAL = 1.0

    async def event_gen():
        last_signature: Optional[tuple] = None
        try:
            yield f": connected task_id={task_id}\n\n"
        except Exception:
            return

        while True:
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break

            try:
                data = await _resolve_segmentation_task_progress(task_id)
            except Exception as e:
                data = {
                    "task_id": task_id,
                    "state": "FAILURE",
                    "progress": 0.0,
                    "message": f"进度查询失败: {type(e).__name__}: {str(e)[:200]}",
                }

            payload = {
                "task_id": data["task_id"],
                "state": data["state"],
                "progress": data["progress"],
                "message": data["message"],
            }
            for k in ("current_epoch", "total_epochs",
                      "data_total", "data_train", "data_val",
                      "num_classes", "class_names", "model_version_id",
                      "started_at", "finished_at", "duration_seconds", "source"):
                v = data.get(k)
                if v is not None:
                    payload[k] = v
            if data.get("result") and isinstance(data["result"], dict):
                for k in ("status", "total", "auto_labeled", "no_match"):
                    if k in data["result"]:
                        payload.setdefault(k, data["result"][k])

            signature = (payload["state"], round(payload["progress"], 1), payload["message"])
            if signature != last_signature:
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                last_signature = signature

            if payload["state"] in ("SUCCESS", "FAILURE", "REVOKED"):
                try:
                    yield "event: end\ndata: {}\n\n"
                except Exception:
                    pass
                break

            await asyncio.sleep(SSE_SEG_POLL_INTERVAL)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
