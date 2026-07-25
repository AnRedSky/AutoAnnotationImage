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
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.middleware.http.auth import get_user_optional_for_query
from app.schemas.training import TrainStatusResponse
from app.tasks.service.job_state_service import JobStateService

router = APIRouter()


# ============== REST 轮询接口 ==============

@router.get("/progress/{task_id}", response_model=TrainStatusResponse)
async def get_progress(
    task_id: str,
    request: Request,
    token: str | None = Query(default=None),
    current_user: User | None = Depends(get_user_optional_for_query),
    db: AsyncSession = Depends(get_db),
):
    """查询训练进度 (前端轮询, 每 2 秒一次) - 鉴权可选

    **v3.0.0 Phase 3 重构**: 状态合并逻辑下沉到 JobStateService.get_snapshot
    (Celery + DB 合并 / 终态回退 / 4 处真相源统一) 都在 service 层
    """
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

SSE_POLL_INTERVAL = 1.0  # 服务端每 1s 拉一次 Celery + DB
SSE_KEEPALIVE_INTERVAL = 15.0  # 空闲时每 15s 发一次 :keepalive 注释帧, 防代理超时


@router.get("/progress/stream/{task_id}")
async def stream_training_progress(
    task_id: str,
    request: Request,
    token: str | None = Query(default=None),
    current_user: User | None = Depends(get_user_optional_for_query),
):
    """SSE 端点: 实时推送训练进度 (v3.0.0 Phase 4: 委托 JobStateService.get_snapshot_with_fresh_db)

    - 每 1s 拉一次 JobStateService 拿权威快照
    - 终端态下推完最后一帧后服务端主动结束流
    - 仅在 (state, progress, message, current_epoch) 签名变化时推送, 避免静默期洪水
    - 长空闲期 (state=PENDING 且 worker 未接走) 周期性发 keepalive 注释帧
    - 客户端断开 (页面刷新 / 切页) 通过 request.is_disconnected() 立即退出
    """
    from celery.result import AsyncResult

    async def event_generator():
        last_signature = None
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

            # ---- 委托 JobStateService 拿权威快照 (DB 优先 + Celery 兜底) ----
            try:
                snap = await JobStateService.get_snapshot_with_fresh_db(task_id)
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

            # ---- 签名去重: 状态/进度/消息/当前 epoch/时间字段 任一变化才推 ----
            signature = (
                state,
                round(progress, 1),
                message,
                current_epoch,
                str(started_at) if started_at is not None else None,
                str(finished_at) if finished_at is not None else None,
            )
            if signature != last_signature:
                # SSE 字段: data= 一行 JSON, 后跟一个空行表示一帧结束
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last_signature = signature
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

            await asyncio.sleep(SSE_POLL_INTERVAL)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx buffering, 否则 SSE 会被缓冲到不实时
        },
    )
