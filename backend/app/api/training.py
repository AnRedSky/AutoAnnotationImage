"""
Training API: Start / Query Training Task
========================================
异步训练任务, 通过 Celery 调度
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.workers.tasks import train_model_task
from app.core.deps import get_current_user, get_user_optional_for_query
from app.core.redis_client import redis_client
from app.database import get_db
from app.models.user import User
from app.models.dataset import Dataset
from app.models.training_job import TrainingJob
from app.schemas.training import TrainStartResponse, TrainStatusResponse, TrainingJobOut, TrainingJobList, TrainingJobActionResult, TrainingJobUpdate, TrainingJobLogAppend, TrainingJobLogOut
from app.config import settings

router = APIRouter()


# v2.5.35: get_user_optional_for_query 已迁到 app.core.deps (供 detection/segmentation 复用)
# 这里直接 import 使用, 旧定义删除, 行为完全等价.


# ============== task_type 分发 ==============
# 三种任务 (classification/detection/segmentation) 共用 TrainingJob 表,
# worker 端 _create_job() 都用 self.request.id (== 预生成的 celery_task_id) 查找
# 预创建行, 命中则 UPDATE state=PROGRESS. 因此预创建 + apply_async(task_id=...)
# 对三种任务都安全, 前端统一走 /training/start 即可, 无需感知 task_type.
#
# 之前 start_training / start_existing_training_job 只投递 train_model_task
# (纯分类), detection/segmentation 数据集提交后会被分类管线读取 final_label_id
# (检测图该字段为 NULL) → 样本被全部跳过 → "已标注图片不足". 本分发修复此问题.


def _build_task_kwargs(
    task_type: str,
    *,
    dataset_id: int,
    user_id: int,
    base_model: str,
    model_name: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    pretrained_model_path: Optional[str] = None,
) -> dict:
    """按 task_type 构建对应 Celery 任务的 kwargs.

    - classification: timm 微调 (base_model=..., model_name=别名, 支持增量权重)
    - detection:     ultralytics YOLO (model_name=权重名 yolov8n/..., model_alias=落盘名)
    - segmentation:   torchvision DeepLabV3+ (backbone=..., model_alias=落盘名)

    detection/segmentation 的 task 签名不接受 pretrained_model_path
    (YOLO 用 ultralytics 自带预训练权重, DeepLab 用 torchvision 预训练), 故忽略.
    """
    if task_type == "detection":
        return dict(
            dataset_id=dataset_id,
            user_id=user_id,
            model_name=base_model,    # yolov8n/s/m/l/x
            model_alias=model_name,  # 落盘 ModelVersion.name
            epochs=epochs,
            batch=batch_size,
        )
    if task_type == "segmentation":
        return dict(
            dataset_id=dataset_id,
            user_id=user_id,
            backbone=base_model,     # deeplabv3_resnet50/101
            model_alias=model_name,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
        )
    # classification (默认)
    return dict(
        dataset_id=dataset_id,
        base_model=base_model,
        model_name=model_name,
        user_id=user_id,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        pretrained_model_path=pretrained_model_path if pretrained_model_path else None,
    )


def _apply_training_task(task_type: str, kwargs: dict, *, task_id: Optional[str] = None):
    """按 task_type 选择 Celery 任务并投递.

    - task_id 非空: apply_async(task_id=...) 强制使用预生成 ID (预创建行场景)
    - task_id 为空: delay() 让 Celery 自动生成 ID (resume 复用旧 job 场景,
      随后由调用方把 job.celery_task_id 更新为 task.id)
    """
    if task_type == "detection":
        from app.workers.detection_tasks import train_detection_task
        task = train_detection_task
    elif task_type == "segmentation":
        from app.workers.segmentation_tasks import train_segmentation_task
        task = train_segmentation_task
    else:
        task = train_model_task
    if task_id:
        return task.apply_async(kwargs=kwargs, task_id=task_id)
    return task.delay(**kwargs)


@router.post("/start", response_model=TrainStartResponse)
async def start_training(
    dataset_id: int,
    base_model: str = "efficientnet_b0",
    model_name: str = "",
    epochs: int = 20,
    batch_size: int = 32,
    learning_rate: float = 1e-4,
    pretrained_model_path: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    启动异步训练任务
    - 提交到 Celery worker
    - 返回 task_id 供前端轮询

    model_name 兜底: 若前端没传/传了空, 自动按 {base_model}_v1_{ts} 生成
    格式与前端 TrainingParamsForm.genAutoName / Training.vue.genDefaultModelName 一致

    pretrained_model_path: 增量训练 (再训练) 时, 传入 .pth 文件路径作为模型起点
    - 空字符串 (默认): 从头微调 (timm ImageNet 预训练权重)
    - 已有路径: 加载该 .pth 的 state_dict (fine-tune 旧模型)

    v2.5.15 P0-4 改造: def → async def
    - 旧: FastAPI 用 threadpool 跑同步路由, 内部再用 _run_async 嵌套 event loop
      在高并发下会触发 "RuntimeError: Event loop is closed" 等不稳定问题
    - 新: async def 直接 await DB 操作, 与 start_existing_training_job 模式一致
    - socket.create_connection 用 asyncio.to_thread 包一下, 避免阻塞 event loop

    v2.5.16: 按 dataset.task_type 分发到对应 Celery 任务
    (classification/detection/segmentation), 修复检测/分割数据集无法训练的问题.
    """
    # ---- 校验数据集并取 task_type ----
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset id={dataset_id} not found")
    task_type = (ds.task_type or "classification").lower()

    # model_name 兜底: 前端为空时, 自动生成
    if not model_name or not model_name.strip():
        ts = int(datetime.utcnow().timestamp()) % 10000000000
        model_name = f"{base_model}_v1_{ts}"

    # 预检: Redis broker 是否可用? 避免 .delay() 长时间阻塞
    # v2.5.15: 用 asyncio.to_thread 包装同步 socket, 避免阻塞 event loop
    import socket
    broker_host = settings.REDIS_HOST
    broker_port = settings.REDIS_PORT
    try:
        await asyncio.to_thread(_check_broker, broker_host, broker_port)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Celery broker (Redis) at {broker_host}:{broker_port} unavailable: {e}. Please start Redis and the Celery worker."
        )

    # ---- 关键: 预创建 TrainingJob 行 (state=PENDING), 消除竞态 ----
    # 之前只在 worker 启动时才 INSERT, 导致前端提交后立刻 GET /jobs 查不到.
    #
    # 本实现:
    # 1. 客户端预生成 UUID (Celery task_id 标准格式), 用这个 ID 同时:
    #    a) 预创建 TrainingJob 行, celery_task_id = 这个 UUID
    #    b) 调用 .apply_async(task_id=...) 强制 Celery 用这个 ID 入队
    # 2. worker 启动后 _create_job() 用 celery_task_id 查找, 一定能命中
    #    (因为预创建行在 API 调用前就写好, 且 ID 是同一份)
    # 3. worker 找到后 UPDATE state=PROGRESS 即可, 不会重复 INSERT
    #
    # 这样前端提交后 GET /jobs 立即能查到新任务 (state=PENDING),
    # worker 启动后该行自动切到 PROGRESS.
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    from app.database import AsyncSessionLocal
    from app.models.training_job import TrainingJob
    import uuid

    # 预生成 task_id, 格式与 Celery 一致 (32 位 hex 字符串)
    celery_task_id = uuid.uuid4().hex

    async def _create_pending_job() -> int:
        """v2.5.15 P0-4: async def, 直接 await, 不嵌套 _run_async"""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                mysql_insert(TrainingJob).values(
                    celery_task_id=celery_task_id,
                    user_id=current_user.id,
                    dataset_id=dataset_id,
                    base_model=base_model,
                    model_name=model_name,
                    task_type=task_type,  # 写入任务类型, 前端列表/详情据此渲染曲线
                    epochs=epochs,
                    batch_size=batch_size,
                    learning_rate=learning_rate,
                    state="PENDING",
                    progress=0.0,
                    message="等待 worker 启动...",
                    created_at=datetime.utcnow(),  # 入库时间, 区别于 started_at (worker 接手)
                    started_at=None,                 # PENDING 阶段不预设, 等 worker 接手
                    finished_at=None,
                )
            )
            await db.commit()
            # 直接用 inserted_primary_key 拿 id (并发安全)
            pk = result.inserted_primary_key
            return pk[0] if pk else None

    job_id = await _create_pending_job()

    try:
        # 用 .apply_async(task_id=...) 强制 Celery 用我们预生成的 ID 入队
        # 这样 worker 端的 task_id 一定等于预创建行里的 celery_task_id
        # 按 task_type 分发到对应任务 (classification/detection/segmentation)
        task = _apply_training_task(
            task_type,
            _build_task_kwargs(
                task_type,
                dataset_id=dataset_id,
                user_id=current_user.id,
                base_model=base_model,
                model_name=model_name,
                epochs=epochs,
                batch_size=batch_size,
                learning_rate=learning_rate,
                pretrained_model_path=pretrained_model_path,
            ),
            task_id=celery_task_id,  # 强制使用预生成的 ID
        )
    except Exception as e:
        # 入队失败, 回滚预创建的行, 避免脏数据
        async def _rollback_pending() -> None:
            """v2.5.15 P0-4: async def + await"""
            from sqlalchemy import delete
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(TrainingJob).where(TrainingJob.id == job_id)
                )
                await db.commit()
        try:
            await _rollback_pending()
        except Exception:
            pass  # 兜底失败也无所谓, 留条脏数据后续清理
        err_msg = str(e)[:200]
        if any(k in err_msg.lower() for k in ["connection", "refused", "redis", "broker", "timeout"]):
            raise HTTPException(
                status_code=503,
                detail=f"Celery broker unavailable: {err_msg}. Please start Redis and the Celery worker."
            )
        raise HTTPException(500, f"Failed to submit training task: {err_msg}")

    # task.id 应等于我们预生成的 celery_task_id
    assert task.id == celery_task_id, f"Celery task id mismatch: {task.id} != {celery_task_id}"

    return TrainStartResponse(
        task_id=task.id,
        celery_task_id=task.id,
        job_id=job_id,  # 预创建行的 id, 前端 loadJobs 立即能看到
        state="PENDING",
        message="Training task submitted",
    )


def _check_broker(host: str, port: int) -> None:
    """v2.5.15 P0-4: 同步的 broker TCP 探测, 由 asyncio.to_thread 调用
    提到模块顶层, 方便测试单独覆盖
    """
    import socket
    s = socket.create_connection((host, port), timeout=2.0)
    s.close()


@router.get("/progress/{task_id}", response_model=TrainStatusResponse)
async def get_progress(
    task_id: str,
    request: Request,
    token: str | None = Query(default=None),
    current_user: User | None = Depends(get_user_optional_for_query),
    db: AsyncSession = Depends(get_db),
):
    """查询训练进度 (前端轮询, 每 2 秒一次) - 鉴权可选

    注意: Celery SUCCESS 状态不带 meta (只有 result dict), 所以 progress/msg
    会为空. 这里在终端态回退查 DB, 拿到 TrainingJob.progress 和 message,
    确保前端看到 100% 而不是 0%.

    实现: 用 async def + Depends(get_db) 直接在主 uvicorn loop 上跑 DB 查询.
    避免 sync 路由 + run_coroutine_threadsafe 的 threadpool 桥接不稳定问题.
    """
    from celery.result import AsyncResult

    state = "PENDING"
    info: dict = {}
    try:
        result = AsyncResult(task_id)
        try:
            state = result.state
        except Exception:
            state = "PENDING"
        try:
            raw_info = result.info
            if isinstance(raw_info, dict):
                info = raw_info
        except Exception:
            info = {}
    except Exception:
        state = "PENDING"
        info = {}

    # 终端态 + Celery result 缺 progress/msg → 回退 DB
    # 注意: Celery SUCCESS 状态下, result.info 是 task 返回值 (dict 包含 status/result/job_id)
    # 而不是 meta. 所以即使 info 非空, 也不一定有 progress 字段
    db_progress = None
    db_msg = None
    db_total_epochs = None
    db_current_epoch = None
    # v2.5.28: 拉 started_at / finished_at (DB 权威), 返回给 REST 客户端
    db_started_at = None
    db_finished_at = None
    if state in ("SUCCESS", "FAILURE", "REVOKED") and "progress" not in info:
        try:
            row = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if row is not None:
                db_progress = row.progress
                db_msg = row.message
                if row.error and not db_msg:
                    db_msg = row.error[:200]
                db_total_epochs = row.epochs
                if isinstance(row.history, list) and row.history:
                    db_current_epoch = row.history[-1].get("epoch")
                db_started_at = row.started_at
                db_finished_at = row.finished_at
        except Exception:
            pass
    # v2.5.28: 非终态也读一下 started_at (PENDING 阶段为 None, worker 接手后写入)
    if db_started_at is None and state != "PENDING":
        try:
            row2 = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if row2 is not None:
                db_started_at = row2.started_at
                if state in ("SUCCESS", "FAILURE", "REVOKED"):
                    db_finished_at = row2.finished_at
        except Exception:
            pass

    return TrainStatusResponse(
        task_id=task_id,
        state=state,
        progress=float(db_progress if db_progress is not None else info.get("progress", 0)),
        current_epoch=info.get("epoch") or db_current_epoch,
        total_epochs=info.get("total_epochs") or db_total_epochs,
        message=(db_msg if db_msg is not None else info.get("msg", "")) or "",
        history=None,
        started_at=db_started_at,
        finished_at=db_finished_at,
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
    """SSE 端点: 实时推送训练进度

    - 每 1s 拉一次 Celery state (PENDING/PROGRESS/SUCCESS/FAILURE/REVOKED)
    - 终端态下 Celery result.info 缺 progress/msg 时回退到 MySQL TrainingJob
    - 仅在 (state, progress, message, current_epoch) 签名变化时推送, 避免静默期洪水
    - 终端态推完最后一帧后服务端主动结束流, 客户端无需靠超时判断
    - 长空闲期 (state=PENDING 且 worker 未接走) 周期性发 keepalive 注释帧
    - 客户端断开 (页面刷新 / 切页) 通过 request.is_disconnected() 立即退出
    """
    from celery.result import AsyncResult
    from app.database import AsyncSessionLocal
    from app.models.training_job import TrainingJob
    from sqlalchemy import select

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

            state = "PENDING"
            info: dict = {}
            try:
                result = AsyncResult(task_id)
                try:
                    state = result.state
                except Exception:
                    state = "PENDING"
                try:
                    raw_info = result.info
                    if isinstance(raw_info, dict):
                        info = raw_info
                except Exception:
                    info = {}
            except Exception:
                state = "PENDING"
                info = {}

            # ---- 关键: 永远先查 DB, 拿到权威 (state, progress, message) ----
            # Celery 的 PENDING 状态有歧义:
            #   a) 任务刚入队, worker 还没接走 (此时 DB 可能还没记录)
            #   b) 任务已被 worker 处理, 但 worker 在 mark_as_done/FAILURE 路径上
            #      崩了, Redis 端 result 缺失/损坏, Celery 退化为 PENDING
            #   c) 任务完全没存在过 (前端传的 task_id 是假的)
            # 对于 (b), DB 一定有 TrainingJob 记录且 state 可能是 PROGRESS/FAILURE.
            # 对于 (a)/(c), DB 没有记录.
            # 因此: 始终查 DB, 优先用 DB 状态; 查不到才用 Celery 状态.
            # 这样 UI 永远显示真实状态, 不会因为 worker 崩溃而误导.
            db_state = None
            db_progress = None
            db_msg = None
            db_total_epochs = None
            db_current_epoch = None
            # v2.5.28: 拉 started_at / finished_at 一起回推, 详情页 SSE 实时刷新
            # - started_at: PENDING 为 None, worker 接手时写入, 之后 PROGRESS/SUCCESS 不变
            # - finished_at: 仅终态有值 (SUCCESS/FAILURE/REVOKED)
            db_started_at = None
            db_finished_at = None
            try:
                async with AsyncSessionLocal() as db:
                    row = (await db.execute(
                        select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
                    )).scalar_one_or_none()
                if row is not None:
                    db_state = row.state
                    db_progress = row.progress
                    db_msg = row.message
                    if row.error and not db_msg:
                        db_msg = row.error[:200]
                    db_total_epochs = row.epochs
                    if isinstance(row.history, list) and row.history:
                        db_current_epoch = row.history[-1].get("epoch")
                    # v2.5.28: 时间字段序列化 (ORM 直接给 datetime, JSON 序列化 OK)
                    db_started_at = row.started_at
                    db_finished_at = row.finished_at
            except Exception:
                pass

            # DB 优先: 如果 DB 有记录, 用 DB state 覆盖 Celery state
            # 终端态 (SUCCESS/FAILURE/REVOKED) 永远以 DB 为准
            # PROGRESS 时: DB 的 progress 来自最近一次 progress_callback 写库
            #              (实际上 callback 只写 Celery, 不写 DB; 所以保留 Celery 的 progress)
            #              但 message 字段 DB 写的更可靠
            if db_state is not None:
                if db_state in ("SUCCESS", "FAILURE", "REVOKED"):
                    state = db_state
                    if db_progress is not None:
                        progress = float(db_progress)
                    else:
                        progress = float(info.get("progress", 0))
                    if db_msg:
                        message = db_msg
                    else:
                        # v2.5.27 修复: 兼容分割 _train_cb 推的 info 字段 (而非 msg)
                        # 原: message = info.get("msg", "") -> 分割场景永远空
                        # 现: msg 优先, info 兜底
                        message = info.get("msg") or info.get("info") or ""
                    if db_total_epochs is not None:
                        total_epochs = db_total_epochs
                    else:
                        total_epochs = info.get("total_epochs")
                    if db_current_epoch is not None:
                        current_epoch = db_current_epoch
                    else:
                        current_epoch = info.get("current_epoch") or info.get("epoch")
                else:
                    # PROGRESS / PENDING 状态: Celery info 里有实时 meta, 优先用
                    state = db_state  # 但 state 字段用 DB 的 (避免 Celery PENDING 误导)
                    progress = float(info.get("progress", 0))
                    current_epoch = info.get("current_epoch") or info.get("epoch")
                    total_epochs = info.get("total_epochs")
                    # v2.5.27 修复: 同上, 兼容分割 info 字段
                    message = info.get("msg") or info.get("info") or ""
            else:
                # DB 没记录 (任务完全没存在过), 用 Celery 状态
                progress = float(info.get("progress", 0))
                current_epoch = info.get("current_epoch") or info.get("epoch")
                total_epochs = info.get("total_epochs")
                # v2.5.27 修复: 同上
                message = info.get("msg") or info.get("info") or ""

            payload = {
                "task_id": task_id,
                "state": state,
                "progress": round(progress, 2),
                "current_epoch": current_epoch,
                "total_epochs": total_epochs,
                "message": message,
            }
            # v2.5.28: 透传 started_at / finished_at (DB 权威), 详情页 SSE 实时刷新
            # - openDetail 时拉过 DB, 但 worker 接手后 (started_at 写入) SSE 没推, 详情会卡在 None
            # - 终态下 finished_at 一旦有值 (FAILURE/SUCCESS 写库后) 立即推, 不必等下次 onComplete
            # - datetime 直接 JSON 序列化 (FastAPI 会 ISO 化)
            if db_started_at is not None:
                payload["started_at"] = db_started_at.isoformat() if hasattr(db_started_at, "isoformat") else db_started_at
            if db_finished_at is not None:
                payload["finished_at"] = db_finished_at.isoformat() if hasattr(db_finished_at, "isoformat") else db_finished_at
            # ---- 透传 progress_callback 的 extra (数据集统计 + 增量训练状态) ----
            # 训练启动那一刻, train.py 会把 data_total/data_train/data_val/
            # num_classes/class_names 通过 extra 一次性推过来; 增量训练时
            # pretrained_loaded/pretrained_path 也会在那一刻推过来. 后续 epoch
            # 这些字段保持不变, 静默期 SSE 不重发, 终端态下前端仍能看到完整统计.
            for _ek, _ev in (info or {}).items():
                if _ek in ("data_total", "data_train", "data_val",
                           "num_classes", "class_names",
                           "pretrained_loaded", "pretrained_path",
                           "pretrained_error", "model_name"):
                    payload[_ek] = _ev

            # ---- 签名去重: 状态/进度/消息/当前 epoch/时间字段 任一变化才推 ----
            # v2.5.28: 加入 started_at / finished_at, 让"worker 接手"和"任务结束"
            # 这两个时间点的变化也能触发推送 (否则首帧没值就一直空着)
            signature = (
                state,
                round(progress, 1),
                message,
                current_epoch,
                str(db_started_at) if db_started_at is not None else None,
                str(db_finished_at) if db_finished_at is not None else None,
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


@router.get("/history/{task_id}")
async def get_training_history(
    task_id: str,
    request: Request,
    token: str | None = Query(default=None),
    current_user: User | None = Depends(get_user_optional_for_query),
    db: AsyncSession = Depends(get_db),
):
    """
    返回训练历史曲线数据（用于前端绘制 loss/acc 折线图）- 鉴权可选

    数据来源 (双源):
    1. Redis train:history:{task_id} — classification worker 每个 epoch 写入
    2. TrainingJob.history 字段 — detection/segmentation worker 写入 (YOLO/DeepLab
       不走 classification 的 Redis 写入路径)

    Redis 命中则用 Redis; 否则回退查 DB.history, 保证三种任务曲线都能展示.
    Redis 不可达时返回空历史（前端展示 "暂无历史曲线"），避免 500.
    """
    history_key = f"train:history:{task_id}"
    history = []
    try:
        raw = redis_client.get(history_key)
        if raw:
            try:
                history = json.loads(raw)
            except (ValueError, TypeError):
                history = []
    except Exception as e:
        # Redis 不可达 / 超时 / 权限问题 → 记日志, 继续走 DB 回退
        import logging
        logging.getLogger(__name__).warning(
            "redis_client.get(%s) failed: %s; fallback to DB history", history_key, e
        )

    # ---- DB 回退: Redis 无数据时, 查 TrainingJob.history (detection/segmentation) ----
    if not history:
        try:
            row = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if row is not None and isinstance(row.history, list):
                history = row.history
        except Exception:
            # DB 也查不到, 返回空历史
            history = []
    return {"task_id": task_id, "history": history}


@router.get("/jobs", response_model=TrainingJobList)
@router.get("/jobs/", response_model=TrainingJobList)  # 兼容前端带尾斜杠的写法
async def list_training_jobs(
    dataset_id: Optional[int] = None,
    state: Optional[str] = Query(default=None, description="可选按状态过滤, e.g. PROGRESS/SUCCESS/FAILURE/PAUSED/REVOKED"),
    task_type: Optional[str] = Query(
        default=None,
        description=(
            "可选按任务类型过滤, e.g. classification/detection/segmentation. "
            "v2.5.24: 支持逗号分隔的多值, e.g. 'classification,detection'."
        ),
    ),
    q: Optional[str] = Query(
        default=None,
        description=(
            "关键词模糊搜索: 同时匹配 model_name 与 base_model 两个字段 (LIKE 不区分大小写). "
            "如 'resnet' 会匹配 model_name='resnet50_v1_xxx' 与 base_model='resnet50' 两种结果."
        ),
    ),
    page: int = Query(default=1, ge=1, description="1-based 页码"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页条数 (1-100)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    训练任务历史列表 (训练运行历史数据)
    - 按 dataset_id / state 过滤 (state 可选)
    - 按 q 关键词在 model_name / base_model 两个字段做模糊匹配 (大小写不敏感)
    - 分页: page (>=1) + page_size (1-100, 默认 10)
    - 返回 total + items, 前端 el-pagination 直接用
    """
    from sqlalchemy import func as sa_func, or_

    base = select(TrainingJob)
    count_base = select(sa_func.count(TrainingJob.id))
    if dataset_id is not None:
        base = base.where(TrainingJob.dataset_id == dataset_id)
        count_base = count_base.where(TrainingJob.dataset_id == dataset_id)
    if state:
        base = base.where(TrainingJob.state == state)
        count_base = count_base.where(TrainingJob.state == state)
    if task_type and task_type.strip():
        # v2.5.24: 支持逗号分隔的多值 (前端可能同时选 classification + detection)
        types = [t.strip() for t in task_type.split(",") if t.strip()]
        if len(types) == 1:
            base = base.where(TrainingJob.task_type == types[0])
            count_base = count_base.where(TrainingJob.task_type == types[0])
        elif len(types) > 1:
            base = base.where(TrainingJob.task_type.in_(types))
            count_base = count_base.where(TrainingJob.task_type.in_(types))
    if q and q.strip():
        like_pat = f"%{q.strip()}%"
        # 关键词同时作用于 model_name 与 base_model, 用 OR 连接
        kw_filter = or_(
            TrainingJob.model_name.ilike(like_pat),
            TrainingJob.base_model.ilike(like_pat),
        )
        base = base.where(kw_filter)
        count_base = count_base.where(kw_filter)

    total = (await db.execute(count_base)).scalar_one()
    offset = (page - 1) * page_size
    stmt = base.order_by(TrainingJob.id.desc()).offset(offset).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return TrainingJobList(total=total, items=rows, page=page, page_size=page_size)


@router.get("/jobs/{job_id}", response_model=TrainingJobOut)
async def get_training_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个训练任务详情"""
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_training_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    取消/停止一个训练任务
    - 仅当任务处于 PENDING/PROGRESS 时生效
    - 同步发送 Celery revoke 信号
    - 更新 TrainingJob 状态为 REVOKED
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state not in ("PENDING", "PROGRESS"):
        return {
            "success": False,
            "message": f"Job is already in terminal state: {job.state}",
            "state": job.state,
        }

    # 1) 发 Celery revoke
    if job.celery_task_id:
        try:
            from app.workers.celery_app import celery_app
            celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:
            # broker 不可用时不强失败, 只更新 DB
            pass

    # 2) 更新 DB
    from datetime import datetime as _dt
    job.state = "REVOKED"
    job.message = "Cancelled by user"
    job.finished_at = _dt.utcnow()
    if job.started_at:
        job.duration_seconds = (job.finished_at - job.started_at).total_seconds()
    await db.commit()

    return {"success": True, "state": "REVOKED", "job_id": job_id}


@router.post("/jobs/{job_id}/error")
async def get_training_error(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    读取训练任务的错误详情
    错误信息既存在 TrainingJob.error 字段, 也存在 Redis train:error:{task_id} 兜底
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    err = job.error
    redis_err = None
    if job.celery_task_id:
        try:
            import json
            from app.core.redis_client import redis_client
            raw = redis_client.get(f"train:error:{job.celery_task_id}")
            if raw:
                try:
                    redis_err = json.loads(raw)
                except (ValueError, TypeError):
                    redis_err = {"raw": raw}
        except Exception:
            pass
    return {
        "job_id": job_id,
        "state": job.state,
        "db_error": err,
        "redis_error": redis_err,
    }


# ============== 训练日志持久化 ==============

@router.get("/jobs/{job_id}/log", response_model=TrainingJobLogOut)
async def get_training_log(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    读取训练任务已持久化的日志 (SSE 推送过的每行, 详情页打开时拉取)
    持久化由前端 /api/training/jobs/{id}/log POST 触发, 后端只存不解析
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    log = job.log if isinstance(job.log, list) else []
    return TrainingJobLogOut(log=log)


@router.post("/jobs/{job_id}/log", response_model=TrainingJobLogOut)
async def append_training_log(
    job_id: int,
    payload: TrainingJobLogAppend,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    追加一行训练日志 (前端 SSE 收到推送时调用)
    - 后端仅追加到 TrainingJob.log 字段 (JSON list)
    - 最多保留 LOG_MAX_LINES (200) 行, 超出时截断头部
    - 空行/超长行 (超过 2KB) 直接拒绝, 避免脏数据
    """
    line = (payload.line or "").rstrip()
    if not line:
        return TrainingJobLogOut(log=[])
    if len(line) > 2048:
        return TrainingJobLogOut(log=[])  # 静默丢弃

    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    log = list(job.log) if isinstance(job.log, list) else []
    log.append(line)
    # 截断头部, 保留尾部 LOG_MAX_LINES 行
    if len(log) > TrainingJob.LOG_MAX_LINES:
        log = log[-TrainingJob.LOG_MAX_LINES:]
    job.log = log
    await db.commit()
    return TrainingJobLogOut(log=log)


# ============== 训练任务控制 (暂停 / 复用启动 / 删除) ==============
# 暂停与取消的区别:
#   - 暂停 (pause)  — 软停止, 设 Redis 标志, worker 在 epoch 边界检测后写 PAUSED
#   - 取消 (cancel) — 硬停止, revoke Celery task 强制退出
# 删除与取消的区别:
#   - 删除 (delete) — 物理删除 DB 记录 (要求任务已结束), 不可恢复


@router.post("/jobs/{job_id}/pause", response_model=TrainingJobActionResult)
async def pause_training_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """暂停一个训练任务

    - 仅当任务处于 PENDING/PROGRESS 时生效
    - 写 Redis train:pause:{task_id} 标志位, worker 在每个 epoch 起点检测
    - 同步 revoke Celery task (terminate=False, 不强杀 SIGTERM; 让 worker 走优雅退出)
    - DB 状态先标 PAUSED (UI 立即可见); worker 实际停下后会写完整消息
    """
    from datetime import datetime as _dt
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state not in ("PENDING", "PROGRESS"):
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Cannot pause a job in state {job.state}",
        )

    # 1) Redis 标志位 (worker 下个 epoch 起点读到即抛 TrainingPaused)
    if job.celery_task_id:
        try:
            redis_client.setex(f"train:pause:{job.celery_task_id}", 3600, "1")
        except Exception:
            pass  # Redis 不可用不强失败, DB 状态已写, worker 下次 query 也能感知

    # 2) revoke Celery (terminate=False, 不强杀, 等 worker 走 epoch 边界正常退出)
    if job.celery_task_id:
        try:
            from app.workers.celery_app import celery_app
            celery_app.control.revoke(job.celery_task_id, terminate=False)
        except Exception:
            pass

    # 3) DB 状态 (UI 立即可见 PAUSED, 不等 worker 跑完这个 epoch)
    job.state = "PAUSED"
    job.message = "Pause requested by user"
    await db.commit()

    return TrainingJobActionResult(
        success=True, job_id=job_id, state="PAUSED",
        message="Pause signal sent. Worker will stop at next epoch boundary.",
    )


@router.post("/jobs/{job_id}/start", response_model=TrainingJobActionResult)
async def start_existing_training_job(
    job_id: int,
    mode: str = Query(default="restart", description="restart=再训练 (新 job_id) | resume=继续 (复用同 job, 不改 model_name)"),
    payload: Optional[TrainingJobUpdate] = Body(default=None, description="参数覆盖 (仅 mode=restart 生效, mode=resume 必须用原 job 参数)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """复用旧任务配置重新提交 Celery 训练任务

    - mode=restart (默认, 用于终态任务的「再训练」按钮):
        * 用旧 job 的 (dataset_id, base_model, model_name, epochs, batch_size, learning_rate)
        * 可选 payload 参数覆盖 (再训练弹窗): 用于「原任务不动, 仅作为新任务的训练参数」场景
        * 新 model_name = 原 model_name + _r{timestamp} 后缀 (避免 .pth 文件冲突)
        * 旧 job 记录保持不变, worker 接手时写新 PENDING 记录
    - mode=resume (用于 PAUSED 任务的「继续」按钮):
        * 复用原 job 的全部配置, 包括 model_name (覆盖现有 .pth 即可)
        * 旧 job 记录继续使用, worker 重新执行训练
        * 不允许传 payload (语义: 继续 = 断点续训, 不能换参数)
    - 若旧 job 仍在 PENDING/PROGRESS, 返回错误
    """
    if mode not in ("restart", "resume"):
        raise HTTPException(400, f"invalid mode: {mode}, must be 'restart' or 'resume'")

    # ---- 顶层 import 集中区 (避免函数内 import 引发 UnboundLocalError) ----
    # 教训: Python 是函数级作用域, 函数内任何 from-import 都会让同名变量在整个
    # 函数体内被视作 local. 如果函数顶部代码 (在 import 之前) 引用了同名变量,
    # 即使 import 在 if 分支内未执行, 也会触发 UnboundLocalError.
    # 故把模式分支所需的 import 全部提到这里.
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    from app.database import AsyncSessionLocal
    # 不再需要 _run_async: 下方 _create_pending_restart_job / _rollback_restart 已改为 async def
    import uuid as _uuid

    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state in ("PENDING", "PROGRESS"):
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Job is still running in state {job.state}. Cancel it first.",
        )

    # resume 模式要求任务处于 PAUSED 状态 (其他终态走 restart)
    if mode == "resume" and job.state != "PAUSED":
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Only PAUSED job can be resumed; current state is {job.state}",
        )

    # ---- 计算最终训练参数 (旧 job 字段为基底, payload 覆盖) ----
    # 再训练: 原 job 不修改, 仅把新参数作为新任务的训练参数
    # model_name 在再训练场景下强制加 _r{timestamp} 后缀 (避免覆盖旧 .pth)
    overrides = payload.model_dump(exclude_unset=True) if payload else {}

    if mode == "resume":
        if overrides:
            raise HTTPException(
                400,
                "resume 模式不允许修改参数 (继续训练 = 断点续训). 如需新参数, 请用「再训练」"
            )
        final_dataset_id = job.dataset_id
        final_base_model = job.base_model
        final_epochs = job.epochs
        final_batch_size = job.batch_size
        final_learning_rate = job.learning_rate
        new_model_name = job.model_name
        final_task_type = (job.task_type or "classification").lower()
    else:
        # restart: 应用 payload 覆盖 (不修改 job 记录)
        final_dataset_id = overrides.get("dataset_id", job.dataset_id)
        final_base_model = overrides.get("base_model", job.base_model)
        final_epochs = overrides.get("epochs", job.epochs)
        final_batch_size = overrides.get("batch_size", job.batch_size)
        final_learning_rate = overrides.get("learning_rate", job.learning_rate)
        final_task_type = (job.task_type or "classification").lower()
        # model_name: 强制加后缀 (即使 payload 改了 model_name, 也再加一层时间戳)
        # 原因: 防止再训练任务与历史任务 .pth 冲突; 同时也保证是「新」任务的标识
        suffix = f"_r{int(datetime.utcnow().timestamp())}"
        payload_model = overrides.get("model_name", job.model_name)
        # 如果用户没改 model_name, 直接追加后缀; 如果改了, 用户指定的新名字 + 后缀
        new_model_name = f"{payload_model}{suffix}"

    # ---- 增量训练 (再训练) 核心: 自动用当前激活的模型权重 ----
    # 找该 dataset 下 is_active=True 的最新 ModelVersion, 拿它的 file_path
    # - 找不到: 不报错, 走 timm ImageNet 预训练权重 (从新训练)
    # - 找到: 加载该 .pth 作为模型起点 (fine-tune 旧模型, 不重头)
    pretrained_model_path = None
    pretrained_source_mv_id = None
    if mode == "restart":
        from app.models.model_version import ModelVersion
        active_mv = (await db.execute(
            select(ModelVersion)
            .where(
                ModelVersion.dataset_id == final_dataset_id,
                ModelVersion.is_active == True,  # noqa: E712
            )
            .order_by(ModelVersion.id.desc())
        )).scalars().first()
        if active_mv and active_mv.file_path:
            pth = Path(active_mv.file_path)
            if pth.exists():
                pretrained_model_path = str(pth)
                pretrained_source_mv_id = active_mv.id

    # ---- 预创建 TrainingJob 行 (mode=restart) ----
    # 之前只入队不写库, 与 start_training 的"新建"路径存在同样的竞态
    # 问题: 前端 GET /jobs 在 worker 启动前查不到新行, 用户感觉"再训练没
    # 生效". 现在采用与 start_training 一致的预创建模式:
    # - 预生成 celery_task_id (UUID), 用同一 ID 做:
    #   a) INSERT 一条 PENDING 行 (restart 模式)
    #   b) .apply_async(task_id=...) 强制入队使用相同 ID
    # 这样 worker _create_job() 一定能用 celery_task_id 找到这行.
    #
    # mode=resume 不预创建: 走的是复用旧 job 行的路径 (见下方 resume 分支)
    new_job_id: int | None = None
    celery_task_id_to_use: str | None = None

    if mode == "restart":
        # 顶层 import 已在函数入口集中引入 (见上方), 此处不再重复导入
        celery_task_id_to_use = _uuid.uuid4().hex

        async def _create_pending_restart_job() -> int | None:
            """预创建 PENDING 行 (mode=restart)
            - 与 start_training 不同: 显式传 started_at=None, 避免 SQLAlchemy 自动填
              default=datetime.utcnow (这会让 PENDING 阶段就显示"已开始", 详见 tasks.py
              中对 PENDING/started_at 的语义约定)
            - 容错: 如果 inserted_primary_key 拿不到 (极端情况), 返回 None, 外层会
              走"预创建失败 → 直接 500"分支, 避免幽灵占位
            - 重试: aiomysql 偶发 MySQLServerHasGoneAway 时, 一次重试, 仍失败则抛

            注意: 必须写成 async def, 直接 await _do().
            早期写成 def + _run_async(_do()) 在 async 上下文里会触发
            "Cannot run the event loop while another loop is running"
            (因为 _run_async 内部 asyncio.new_event_loop + run_until_complete
            与 FastAPI/uvicorn 主 event loop 冲突).
            """
            async def _do():
                async with AsyncSessionLocal() as sdb:
                    result = await sdb.execute(
                        mysql_insert(TrainingJob).values(
                            celery_task_id=celery_task_id_to_use,
                            user_id=current_user.id,
                            dataset_id=final_dataset_id,
                            base_model=final_base_model,
                            model_name=new_model_name,
                            task_type=final_task_type,  # 继承原任务类型
                            epochs=final_epochs,
                            batch_size=final_batch_size,
                            learning_rate=final_learning_rate,
                            state="PENDING",
                            progress=0.0,
                            message="等待 worker 启动...",
                            created_at=datetime.utcnow(),
                            started_at=None,
                            finished_at=None,
                        )
                    )
                    await sdb.commit()
                    pk = result.inserted_primary_key
                    return pk[0] if pk else None
            try:
                return await _do()
            except Exception:
                # 重试一次 (偶发 MySQLServerHasGoneAway / 连接池抖动)
                try:
                    return await _do()
                except Exception as _e2:
                    # 重试也失败: 抛给外层, 整个 start 接口返回 503, 前端会显示错误
                    raise _e2

        new_job_id = await _create_pending_restart_job()
        if new_job_id is None:
            # 防御性兜底: 预创建返回 None, 直接 500, 避免 apply_async 用一个孤儿 task_id
            raise HTTPException(500, "预创建训练任务行失败 (inserted_primary_key 为空), 请重试")

    try:
        # 按 final_task_type 分发到对应 Celery 任务 (classification/detection/segmentation)
        apply_kwargs = _build_task_kwargs(
            final_task_type,
            dataset_id=final_dataset_id,
            user_id=current_user.id,
            base_model=final_base_model,
            model_name=new_model_name,
            epochs=final_epochs,
            batch_size=final_batch_size,
            learning_rate=final_learning_rate,
            pretrained_model_path=pretrained_model_path,
        )
        if mode == "restart":
            # 强制使用预生成的 ID, 与预创建行的 celery_task_id 一致
            task = _apply_training_task(final_task_type, apply_kwargs, task_id=celery_task_id_to_use)
        else:
            # resume: 不指定 task_id, Celery 自动生成; 随后更新 job.celery_task_id = task.id
            task = _apply_training_task(final_task_type, apply_kwargs)
    except Exception as e:
        # 入队失败, 回滚预创建的行
        if new_job_id is not None:
            async def _rollback_restart() -> None:
                """同 _create_pending_restart_job 的原因: 必须在 async 上下文 await,
                不能用 _run_async (_run_async 内部 new_event_loop 会和 FastAPI 主 loop
                冲突)"""
                from sqlalchemy import delete
                async with AsyncSessionLocal() as sdb:
                    await sdb.execute(
                        delete(TrainingJob).where(TrainingJob.id == new_job_id)
                    )
                    await sdb.commit()
            try:
                await _rollback_restart()
            except Exception:
                pass
        err_msg = str(e)[:200]
        if any(k in err_msg.lower() for k in ["connection", "refused", "redis", "broker"]):
            raise HTTPException(503, f"Celery broker unavailable: {err_msg}")
        raise HTTPException(500, f"Failed to submit training task: {err_msg}")

    # resume 模式: 立刻把 DB 中的 job 状态从 PAUSED 改回 PENDING,
    # celery_task_id 更新为新 task_id, 这样前端列表能立刻看到状态变化
    #
    # 注意: 不要预设 started_at, 让 worker 接手时设置 (worker._create_job
    # 会写入 started_at = utcnow()). 否则 PENDING 阶段就会显示"开始时间",
    # 用户会误以为任务已开始.
    if mode == "resume":
        job.state = "PENDING"
        job.progress = 0.0
        job.celery_task_id = task.id
        # started_at 保留 None, 由 worker 接手时填充
        # job.started_at 维持原值, 因为语义上是"任务整体首次开始"的时间
        # (而不是"这一轮 resume 开始"的时间)
        job.finished_at = None
        job.error = None
        # message 保留以显示
        await db.commit()
        await db.refresh(job)

    return TrainingJobActionResult(
        success=True,
        job_id=job_id,
        new_job_id=new_job_id,  # mode=restart 时为新预创建行的 id
        state="PENDING",
        message=(
            f"Resumed training (model_name={new_model_name})"
            if mode == "resume"
            else (
                f"New training task #{new_job_id} created (model_name={new_model_name}, "
                f"原任务 #{job_id} 保持不变, "
                f"{'增量训练: 基于 ModelVersion #' + str(pretrained_source_mv_id) if pretrained_source_mv_id else '从头微调 (无激活模型, 使用 ImageNet 预训练)'})"
            )
        ),
        task_id=task.id,
    )


@router.patch("/jobs/{job_id}", response_model=TrainingJobOut)
async def update_training_job(
    job_id: int,
    payload: TrainingJobUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新训练任务参数 (任务未运行才允许)

    - 允许状态: PENDING (未启动) / PAUSED (已暂停) / SUCCESS / FAILURE / REVOKED (终态, 可调整参数后重跑)
    - 禁止状态: PROGRESS (运行中, 参数已生效, 改了下次才生效但易引起混淆)
    - 仅更新提供的字段 (PUT 语义的部分更新)
    - 同步重置 message (清掉旧的 "等待 worker" 文案, 让用户知道参数刚改过)
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state == "PROGRESS":
        raise HTTPException(409, f"任务 #${job_id} 正在训练中, 暂不允许编辑参数")

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        # 没有任何字段, 啥都不做, 直接返回
        return job

    # 业务校验: model_name 唯一性 (同 dataset 下不与历史冲突)
    if "model_name" in update_data or "dataset_id" in update_data:
        target_dataset = update_data.get("dataset_id", job.dataset_id)
        target_name = update_data.get("model_name", job.model_name)
        # 找同 dataset + 同 model_name 的其他 job (排除自己)
        # select 已在模块顶部 import (line 14), 此处不再重复导入
        dup = (await db.execute(
            select(TrainingJob).where(
                TrainingJob.id != job_id,
                TrainingJob.dataset_id == target_dataset,
                TrainingJob.model_name == target_name,
            )
        )).scalars().first()
        if dup:
            raise HTTPException(400, f"模型版本名 {target_name!r} 在数据集中已存在 (job #{dup.id}), 请换一个")

    for k, v in update_data.items():
        setattr(job, k, v)
    job.message = f"参数已更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, 可点击启动"
    await db.commit()
    await db.refresh(job)
    return job


@router.delete("/jobs/{job_id}", response_model=TrainingJobActionResult)
async def delete_training_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除训练任务记录

    - 仅当任务处于终态 (SUCCESS/FAILURE/REVOKED/PAUSED) 时允许删除
    - 物理删除 DB 记录, 不可恢复
    - 同步清 Redis 中的 history/error/pause 缓存 (避免历史数据残留)
    - 不影响已生成的 ModelVersion 记录 (用户可单独管理)
    """
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state in ("PENDING", "PROGRESS"):
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Cannot delete a running job in state {job.state}. Cancel it first.",
        )

    # 清 Redis 残留
    if job.celery_task_id:
        for key in (
            f"train:history:{job.celery_task_id}",
            f"train:error:{job.celery_task_id}",
            f"train:pause:{job.celery_task_id}",
        ):
            try:
                redis_client.delete(key)
            except Exception:
                pass

    await db.delete(job)
    await db.commit()

    return TrainingJobActionResult(
        success=True, job_id=job_id, state=job.state,
        message=f"Training job #{job_id} deleted",
    )
