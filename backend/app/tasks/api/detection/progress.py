"""
detection.progress 模块 — 训练/自动标注进度查询 (REST + SSE)
=============================================================

**v3.0.0 Phase P 拆分**: 从 detection.py 抽离
**职责**: detection 任务的实时进度查询 (老/新端点 + REST/SSE)

**路由清单** (4 个):
- GET /jobs/{job_id}/progress        老接口: TrainingJob 进度 (int job_id)
- GET /jobs/{job_id}/stream          老接口: TrainingJob SSE
- GET /progress/{task_id}            新接口: REST 轮询 (v2.5.35, task_id = Celery UUID)
- GET /progress/stream/{task_id}     新接口: SSE 推送 (v2.5.35, 与前端 detectionApi 对齐)

**v2.5.35 关键修复**:
- 前端 detectionApi.progress 调 /api/detection/progress/{taskId} (Celery UUID),
  但后端原本只有 /api/detection/jobs/{job_id}/progress (int TrainingJob id),
  导致前端轮询永远 404. 新增与前端路径对齐的端点.
- auto_annotate_detection_task 不写 TrainingJob, 因此 progress 端点
  必须支持"DB 没记录就回退到 Celery result.info"路径.

**v3.3.0 P0 修复**: 必须鉴权 + 校验所有权, 防止跨用户 detection 进度泄露
"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.middleware.http.auth import get_current_user, get_user_optional_for_query
from app.admin.model.user import User
from app.tasks.model.training_job import TrainingJob

router = APIRouter()


async def _assert_can_access_detection_task(task_id: str, current_user: User) -> None:
    """v3.3.0 P0: 校验用户对 detection 任务的访问权 (admin / owner)

    - task_id 是 Celery UUID, 通过 TrainingJob.celery_task_id 反查 TrainingJob
    - non-super_admin 仅能看自己 user_id 的 job 进度 (regular admin 仍受约束)
    - 找不到对应 job (例如 auto_annotate 不写 TrainingJob) → 仅 super_admin 通过

    v3.3.4-PATCH 修复 (admin 旁路):
      - 旧逻辑: is_admin() 旁路
      - 新逻辑: 仅 super_admin 旁路
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


# ============== 统一进度解析 ==============

async def _resolve_detection_task_progress(task_id: str) -> dict:
    """统一解析 Celery task_id -> 进度 dict, 同时覆盖训练 + 自动标注两种来源.

    优先级:
    1. TrainingJob 表 (训练任务走的是 _create_job 路径, celery_task_id 是主键索引列)
    2. Celery AsyncResult (auto_annotate_detection_task 不写 TrainingJob, 只走 update_state)

    返回字段:
      { task_id, state, progress, message, source: 'db'|'celery'|'unknown',
        total (auto_annotate 终态下的 total/auto_labeled/no_match), result (Celery 终态返回值) }
    """
    from celery.result import AsyncResult

    db_row = None
    try:
        async with AsyncSessionLocal() as db:
            db_row = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
    except Exception:
        db_row = None

    # 拉 Celery result (auto_annotate 也走这里)
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
        # 终态返回值 (SUCCESS 时 result.info 是 task return dict, FAILURE 时是 exc_info)
        try:
            if celery_state == "SUCCESS":
                celery_result_payload = result.result
        except Exception:
            celery_result_payload = None
    except Exception:
        pass

    if db_row is not None:
        # DB 有记录 (训练任务), 用 DB 状态做权威
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

    # DB 没记录 (auto_annotate 任务), 用 Celery state + info
    return {
        "task_id": task_id,
        "state": celery_state,
        "progress": round(float(celery_info.get("progress", 0)), 2),
        "message": celery_info.get("msg") or celery_info.get("info") or "",
        "current_epoch": celery_info.get("current_epoch") or celery_info.get("epoch"),
        "total_epochs": celery_info.get("total_epochs"),
        # auto_annotate 终态返回 {status, total, auto_labeled, no_match}
        "total": celery_info.get("total") or (celery_result_payload.get("total") if isinstance(celery_result_payload, dict) else None),
        "auto_labeled": celery_info.get("auto_labeled") or (celery_result_payload.get("auto_labeled") if isinstance(celery_result_payload, dict) else None),
        "no_match": celery_info.get("no_match") or (celery_result_payload.get("no_match") if isinstance(celery_result_payload, dict) else None),
        "result": celery_result_payload if isinstance(celery_result_payload, dict) else None,
        "source": "celery" if celery_state != "PENDING" else "unknown",
    }


# ============== 老接口: TrainingJob 进度 (int job_id) ==============

@router.get("/jobs/{job_id}/progress")
async def get_job_progress(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    拉取 TrainingJob 进度 (兼容老接口, JSON 轮询)

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
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "duration_seconds": job.duration_seconds,
        "data_total": job.data_total,
        "data_train": job.data_train,
        "data_val": job.data_val,
        "num_classes": job.num_classes,
        "class_names": job.class_names,
        "model_version_id": job.model_version_id,
    }


@router.get("/jobs/{job_id}/stream")
async def stream_job_progress(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    SSE 进度推送: 每秒轮询 TrainingJob, 终态自动断开

    v3.3.0 P0 修复: 必须校验所有权, 防止跨用户 SSE 进度泄露
    v3.3.4-PATCH: 收紧为仅 super_admin 旁路 (regular admin 仍受 owner 校验)
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, f"TrainingJob id={job_id} not found")
    if not current_user.is_super_admin() and job.user_id != current_user.id:
        raise HTTPException(403, "无权限查看此训练任务进度")

    async def event_gen():
        last_state = None
        last_progress = -1.0
        # 最多 1 小时 (防止僵尸连接)
        for _ in range(3600):
            # 重新查一次
            j = await db.get(TrainingJob, job_id)
            if not j:
                break
            if j.state != last_state or j.progress != last_progress:
                payload = {
                    "id": j.id,
                    "state": j.state,
                    "progress": j.progress,
                    "message": j.message,
                    "current_epoch": getattr(j, "current_epoch", None),
                }
                yield f"data: {json.dumps(payload, default=str)}\n\n"
                last_state = j.state
                last_progress = j.progress
            if j.state in ("SUCCESS", "FAILURE", "REVOKED"):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ============== v2.5.35 新增: 与前端 detectionApi 路径对齐的进度端点 ==============
# 前端 detectionApi.progress 调 GET /api/detection/progress/{taskId} (Celery UUID),
# detectionApi.streamProgress 调 GET /api/detection/progress/stream/{taskId}.
# 老的 /jobs/{job_id}/progress 端点保留, 但前端轮询已经走新端点.

@router.get("/progress/{task_id}")
async def get_detection_progress(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    拉取 detection 任务进度 (REST 轮询)

    v2.5.35 新增: 与前端 detectionApi.progress 路径对齐
    - task_id 是 Celery UUID (不是 int TrainingJob id)
    - 同时覆盖 train_detection_task (DB 有 TrainingJob) 和
      auto_annotate_detection_task / auto_annotate_pretrained_task (仅 Celery state)

    v3.3.0 P0 修复: 必须校验所有权
    """
    await _assert_can_access_detection_task(task_id, current_user)
    return await _resolve_detection_task_progress(task_id)


@router.get("/progress/stream/{task_id}")
async def stream_detection_progress(
    task_id: str,
    request: Request,
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_user_optional_for_query),
):
    """
    SSE 实时推送 detection 任务进度 (v2.5.35 新增, 与前端 detectionApi.streamProgress 对齐)

    - 每 1s 拉一次 _resolve_detection_task_progress
    - 仅在 (state, progress, message) 签名变化时推送, 避免静默期洪水
    - 终态推完最后一帧后服务端主动结束流
    - 客户端断开通过 request.is_disconnected() 立即退出
    - 鉴权: query ?token=xxx 优先 (EventSource 无法设 header), header 兜底

    v3.3.0 P0 修复: 必须校验所有权, 防止跨用户 SSE 进度泄露
    """
    if current_user is None:
        raise HTTPException(401, "未授权: 需要有效的 access_token (query ?token= 或 Authorization header)")
    await _assert_can_access_detection_task(task_id, current_user)

    SSE_DET_POLL_INTERVAL = 1.0

    async def event_gen():
        last_signature: Optional[tuple] = None
        # 首帧
        try:
            yield f": connected task_id={task_id}\n\n"
        except Exception:
            return

        while True:
            # 客户端断开检测
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break

            try:
                data = await _resolve_detection_task_progress(task_id)
            except Exception as e:
                # 出错也不中断流, 给前端一个可读的 error 帧
                data = {
                    "task_id": task_id,
                    "state": "FAILURE",
                    "progress": 0.0,
                    "message": f"进度查询失败: {type(e).__name__}: {str(e)[:200]}",
                }

            # 仅透传关键字段, 减少 payload
            payload = {
                "task_id": data["task_id"],
                "state": data["state"],
                "progress": data["progress"],
                "message": data["message"],
            }
            # 透传 auto_annotate 终态统计
            for k in ("total", "auto_labeled", "no_match",
                      "current_epoch", "total_epochs",
                      "data_total", "data_train", "data_val",
                      "num_classes", "class_names", "model_version_id",
                      "started_at", "finished_at", "duration_seconds", "source"):
                v = data.get(k)
                if v is not None:
                    payload[k] = v
            # 透传 Celery 终态返回值 (SUCCESS 时是 task return dict)
            if data.get("result") and isinstance(data["result"], dict):
                for k in ("total", "auto_labeled", "no_match", "status"):
                    if k in data["result"]:
                        payload.setdefault(k, data["result"][k])

            signature = (payload["state"], round(payload["progress"], 1), payload["message"])
            if signature != last_signature:
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                last_signature = signature

            # 终态结束
            if payload["state"] in ("SUCCESS", "FAILURE", "REVOKED"):
                try:
                    yield "event: end\ndata: {}\n\n"
                except Exception:
                    pass
                break

            await asyncio.sleep(SSE_DET_POLL_INTERVAL)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
