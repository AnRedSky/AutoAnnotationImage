"""
training.progress 模块 — 训练进度查询 (REST + SSE 实时推送)
============================================================

**v3.0.0 Phase O 拆分**: 从 training.py 抽离
**职责**:
- get_progress: REST 轮询接口 (前端兜底, 每 2s 一次)
- stream_training_progress: SSE 服务端推送 (推荐使用, 端到端实时, 无轮询风暴)

**v3.0.0 Phase 3 状态合并**: 业务下沉到 JobStateService.get_snapshot / get_snapshot_with_fresh_db
- 4 处真相源 (Celery state / DB / Redis info / start-finish 时间) 统一在 service 层
- 本文件只做 HTTP 包装 + SSE 流式封装

**v3.3.0 P0 修复**: 必须鉴权 + 校验所有权, 防止跨用户训练进度泄露
- 之前: 即使鉴权可选, 任何 token 都能查任何 task_id 进度
- 现在: 必须校验 task 对应 job 的 user_id == current_user.id 或 admin
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user, get_user_optional_for_query
from app.schemas.training import TrainStatusResponse
from app.tasks.model.training_job import TrainingJob
from app.tasks.service.job_state_service import JobStateService

router = APIRouter()


async def _assert_can_access_task_id(task_id: str, current_user: User, db: AsyncSession) -> None:
    """v3.3.0 P0: 校验用户对训练任务的访问权 (admin / owner)

    - task_id 是 Celery UUID, 通过 TrainingJob.celery_task_id 反查 TrainingJob
    - 非 admin 仅能看自己 user_id 的 job 进度
    - 找不到对应 job (例如 auto_annotate 任务, 不写 TrainingJob) → 仅 admin 通过
    """
    if current_user.is_admin():
        return
    job = (await db.execute(
        select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
    )).scalar_one_or_none()
    if job is None:
        # 找不到对应 job (可能是 auto_annotate 等不写 TrainingJob 的 Celery 任务)
        # 非 admin 用户看不到
        raise HTTPException(403, "无权限访问此任务进度")
    if job.user_id != current_user.id:
        raise HTTPException(403, "无权限访问此任务进度")


# ============== REST 轮询接口 ==============

@router.get("/progress/{task_id}", response_model=TrainStatusResponse)
async def get_progress(
    task_id: str,
    request: Request,
    token: str | None = Query(default=None),
    current_user: User | None = Depends(get_user_optional_for_query),
    db: AsyncSession = Depends(get_db),
):
    """查询训练进度 (前端轮询, 每 2 秒一次)

    **v3.0.0 Phase 3 重构**: 状态合并逻辑下沉到 JobStateService.get_snapshot
    (Celery + DB 合并 / 终态回退 / 4 处真相源统一) 都在 service 层

    **v3.3.0 P0 修复**: 必须鉴权 + 校验所有权
    - 之前: 鉴权可选, 任何 token 都能查任何 task_id 进度
    - 现在: 必须登录, 且 task 对应 job 必须是 current_user 创建的
    """
    if current_user is None:
        raise HTTPException(401, "未授权: 需要有效的 access_token")
    await _assert_can_access_task_id(task_id, current_user, db)
    snap = await JobStateService.get_snapshot(task_id, db)
    return TrainStatusResponse(
        task_id=task_id,
        state=snap.state,
        progress=snap.progress,
        current_epoch=snap.current_epoch,
        total_epochs=snap.total_epochs,
        message=snap.message or "",
        history=None,
        started_at=snap.started_at,
        finished_at=snap.finished_at,
    )


# ============== SSE 实时进度推送 ==============
# 与旧 /progress/{task_id} (REST 轮询) 并存, 供前端二选一
# 推荐使用 SSE: 端到端实时, 无轮询风暴, server-push 天然降采样 (同状态不重发)

# v3.5.0 Phase T6 优化: 自适应轮询
# - PROGRESS 状态 1s: 训练中需要 1Hz 实时性
# - PENDING 状态 5s: worker 未启动, 后端不可能有状态变化, 降频到 0.2Hz
# - 终态: 推完一帧 end 即 break (无 sleep 开销)
SSE_POLL_INTERVAL_PROGRESS = 1.0   # 训练中保持 1Hz
SSE_POLL_INTERVAL_PENDING = 5.0    # PENDING 降频 5s (worker 启动可能延迟数十秒)
SSE_KEEPALIVE_INTERVAL = 15.0      # 空闲时每 15s 发一次 :keepalive 注释帧, 防代理超时


@router.get("/progress/stream/{task_id}")
async def stream_training_progress(
    task_id: str,
    request: Request,
    token: str | None = Query(default=None),
    current_user: User | None = Depends(get_user_optional_for_query),
    db: AsyncSession = Depends(get_db),
):
    """SSE 端点: 实时推送训练进度 (v3.5.0 Phase T6 三重优化)

    v3.0.0 Phase 4: 委托 JobStateService.get_snapshot_with_fresh_db
    v3.5.0 Phase T6 优化:
      1) **Redis 缓存** (方案 2): 委托 get_snapshot_with_cache, 1.5s TTL
         - N 个客户端订阅同一 taskId 时, 缓存命中 → 0 DB 查询
         - DB QPS 从 1×N 降到 ≤ 0.67 (1/1.5s 一次)
      2) **db_version 行指纹** (方案 3): snapshot._db_version 与 last_db_version 比对
         - DB 行未变 → 跳过整个推送 (连 JSON 序列化都省)
         - 训练中同一秒内多次 SSE tick 时, 99% 的 tick 走 skip
      3) **状态自适应轮询** (方案 1): PENDING 状态 5s 一次
         - worker 未启动的空窗期 (可能 5-30s) DB QPS 进一步降到 0.2Hz
         - PROGRESS 状态保持 1Hz 实时性

    行为兼容性:
    - 客户端断开通过 request.is_disconnected() 立即退出
    - 终态推完一帧 end 事件后主动关闭流
    - 长空闲期 (PENDING 且无客户端) keepalive 防代理超时
    """
    if current_user is None:
        raise HTTPException(401, "未授权: 需要有效的 access_token")
    await _assert_can_access_task_id(task_id, current_user, db)

    from celery.result import AsyncResult

    async def event_generator():
        last_db_version: Optional[str] = None    # 方案 3: 行 version 跳过推送
        last_signature: Optional[tuple] = None   # 保留旧签名, 兜底
        last_keepalive = 0.0
        loop = asyncio.get_event_loop()

        # 首帧立即推一次, 避免前端 1s 真空
        try:
            yield f": connected task_id={task_id}\n\n"
        except Exception:
            return

        while True:
            # ---- 客户端断开检测 ----
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                # 某些 ASGI 中间件下 is_disconnected 会抛, 视为已断开
                break

            # ---- 委托 JobStateService (Redis 缓存 + DB 兜底) ----
            try:
                snap = await JobStateService.get_snapshot_with_cache(task_id)
            except Exception:
                # 极端异常: 5xx 兜底
                snap = None

            # 兼容极端情况: JobStateService 抛错时退化到 Celery raw
            info: dict = {}
            if snap is None:
                state = "PENDING"
                try:
                    r = AsyncResult(task_id)
                    state = r.state or "PENDING"
                    if isinstance(r.info, dict):
                        info = r.info
                except Exception:
                    pass
                progress = float(info.get("progress", 0.0))
                current_epoch = info.get("epoch")
                total_epochs = info.get("total_epochs")
                message = info.get("msg") or info.get("info") or ""
                started_at = None
                finished_at = None
                # 异常路径无缓存 version, 强制推一次
                current_db_version: Optional[str] = None
            else:
                state = snap.state
                progress = snap.progress
                current_epoch = snap.current_epoch
                total_epochs = snap.total_epochs
                message = snap.message
                started_at = snap.started_at
                finished_at = snap.finished_at
                # 透传 progress_callback 推过来的 extra (data_total/class_names 等)
                try:
                    r = AsyncResult(task_id)
                    if isinstance(r.info, dict):
                        info = r.info
                except Exception:
                    info = {}
                # 方案 3: 取出缓存/DB 行 version (snap 是 dataclass + setattr 附加)
                current_db_version = getattr(snap, "_db_version", None)

            payload = {
                "task_id": task_id,
                "state": state,
                "progress": round(progress, 2),
                "current_epoch": current_epoch,
                "total_epochs": total_epochs,
                "message": message,
            }
            if started_at is not None:
                payload["started_at"] = started_at.isoformat() if hasattr(started_at, "isoformat") else started_at
            if finished_at is not None:
                payload["finished_at"] = finished_at.isoformat() if hasattr(finished_at, "isoformat") else finished_at
            # 透传 extra 字段 (data_total / num_classes / class_names / 增量训练状态)
            for _ek, _ev in (info or {}).items():
                if _ek in ("data_total", "data_train", "data_val",
                           "num_classes", "class_names",
                           "pretrained_loaded", "pretrained_path",
                           "pretrained_error", "model_name"):
                    payload[_ek] = _ev

            # ---- 方案 3: db_version 跳过推送 ----
            # 优先用 db_version (粒度细, 由 DB 行直接算), 缓存命中且未变 → 跳过
            # 异常路径 (snap=None) current_db_version=None, 强制走签名去重兜底
            should_push = False
            if current_db_version is not None:
                if current_db_version != last_db_version:
                    should_push = True
                    last_db_version = current_db_version
            else:
                # 兜底: 旧签名去重 (state, progress, message, current_epoch, time)
                signature = (
                    state,
                    round(progress, 1),
                    message,
                    current_epoch,
                    str(started_at) if started_at is not None else None,
                    str(finished_at) if finished_at is not None else None,
                )
                if signature != last_signature:
                    should_push = True
                    last_signature = signature

            if should_push:
                # SSE 字段: data= 一行 JSON, 后跟一个空行表示一帧结束
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last_keepalive = loop.time()

            # ---- 终端态: 推一帧 end 事件后退出 ----
            if state in ("SUCCESS", "FAILURE", "REVOKED"):
                # 显式 end 事件方便前端 await 收尾
                try:
                    yield f"event: end\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except Exception:
                    pass
                break

            # ---- 长空闲期 keepalive (SSE 注释帧, 浏览器忽略, 防代理 60s 断) ----
            now = loop.time()
            if now - last_keepalive > SSE_KEEPALIVE_INTERVAL:
                try:
                    yield f": keepalive {int(now)}\n\n"
                except Exception:
                    break
                last_keepalive = now

            # ---- 方案 1: 状态自适应 sleep ----
            # PENDING 状态 (worker 未启动) 5s, PROGRESS/PAUSED 1s
            if state == "PENDING":
                await asyncio.sleep(SSE_POLL_INTERVAL_PENDING)
            else:
                await asyncio.sleep(SSE_POLL_INTERVAL_PROGRESS)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx buffering, 否则 SSE 会被缓冲到不实时
        },
    )
