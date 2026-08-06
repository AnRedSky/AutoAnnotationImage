"""
training_lifecycle_service.job — TrainingJob 创建/重置/进度/历史/数据统计
=======================================================================

**v3.0.0 Phase S5 拆分**: 从 training_lifecycle_service.py (580行) 抽离
**职责** (按 Phase 5 设计):
- create_or_reset_job / _sync — 创建或重置 TrainingJob 行 (PROGRESS 状态)
- update_job_progress / _sync — 更新 TrainingJob 进度字段 (DB)
- push_history — 训练历史曲线写入 Redis + DB (双写)
- persist_dataset_stats / _sync — 数据集统计写库 (data_total/num_classes 等)

**调用方**: classification.py / detection/train.py / segmentation/train.py 三个 worker
**设计要点**:
- 4 组方法都是 static, 内部用 _run_async 切到事件循环
- 写库失败不阻塞训练主流程 (仅 logger.warning)
- push_history 兼容旧 worker 调用 (仅 task_id + history_buffer)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.utils.async_helpers import run_async_in_worker as _run_async
from app.database.redis import redis_client

logger = logging.getLogger(__name__)


# ============== 1. TrainingJob 创建/重置 ==============

async def create_or_reset_job(
    *,
    task_id: str,
    user_id: int,
    dataset_id: int,
    base_model: str,
    model_name: str,
    task_type: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    started_at: datetime,
) -> int:
    """创建或重置 TrainingJob 行 (PROGRESS 状态)

    处理两种场景:
    1) API 预创建 (TrainingService.start_training 已写入 PENDING 行):
       - 找到 existing → state=PROGRESS, 清空 error/finished
       - 保留 "等待 worker 启动..." 消息 (API 预创建标记)
    2) Worker 主动创建 (罕见, 兜底):
       - INSERT 新行
    3) Worker 重投递 (崩溃恢复):
       - existing.message != 预创建文案 → 标 "Re-running"

    v3.6.1 PATCH: IntegrityError 重试 (race condition 兜底)
    - 场景: API 层 mode=resume 时先 UPDATE 旧 job 的 celery_task_id 为新 task_id
      再 apply_async, 但 API commit 与 worker poll 启动可能并发:
      * 路径 A (常见): API commit 在 worker 第一次 SELECT 之前 → worker 找到 existing → UPDATE ✅
      * 路径 B (罕见, race): worker 第一次 SELECT 早于 API commit → worker 找不到 → 走 INSERT
        → 但 API commit 在 worker INSERT 之前完成 → INSERT 撞 unique key
    - 修复: INSERT 失败时 catch IntegrityError (1062 Duplicate entry on celery_task_id),
      重新查询并走 UPDATE 路径. 一次重试, 仍失败则抛 (留给上层日志).
    - 这与现有查询 → INSERT 逻辑的语义等价: 无论谁先写, 终态是
      "DB 中只有 1 行 celery_task_id == task_id, 状态 = PROGRESS".

    Returns:
        TrainingJob.id
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalar_one_or_none()

        if existing is not None:
            # ---- 区分"API 预创建"和"worker 重投递" ----
            is_api_precreated = existing.message in (
                "等待 worker 启动...",
                "任务已入队, 等待 worker 启动...",
            )
            existing.state = "PROGRESS"
            existing.progress = 0.0
            existing.error = None
            if not is_api_precreated:
                existing.message = "Re-running (worker restart recovery)"
            existing.started_at = started_at
            existing.finished_at = None
            existing.duration_seconds = None
            existing.task_type = task_type
            # v2.5.28: 重置数据集统计 (避免上一轮残留)
            existing.data_total = None
            existing.data_train = None
            existing.data_val = None
            existing.num_classes = None
            existing.class_names = None
            await db.commit()
            await db.refresh(existing)
            return existing.id

        # 不存在则 INSERT (兜底, 正常流程下 TrainingService 已预创建)
        job = TrainingJob(
            celery_task_id=task_id,
            user_id=user_id,
            dataset_id=dataset_id,
            base_model=base_model,
            model_name=model_name,
            task_type=task_type,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            state="PROGRESS",
            progress=0.0,
            started_at=started_at,
        )
        db.add(job)
        try:
            await db.commit()
        except IntegrityError as e:
            # v3.6.1 PATCH: race condition 兜底
            # 场景: API 层 resume 模式刚 commit 同一 task_id 的 UPDATE 旧 job
            #       → worker 第一次 SELECT 没看到, 走 INSERT, 撞 unique key
            # 处理: 回滚当前 INSERT, 重新查询 (这次 API 一定已经 commit), 走 UPDATE 路径
            #
            # 错误消息识别 (跨 DB 兼容):
            #   MySQL:    (pymysql.err.IntegrityError) (1062, "Duplicate entry 'xxx' for key
            #             'training_jobs.ix_training_jobs_celery_task_id'")
            #   SQLite:   UNIQUE constraint failed: training_jobs.celery_task_id
            # 两者都含 "celery_task_id" + 唯一冲突关键字 ("duplicate" / "unique"),
            # 用这 2 个特征可跨 DB 识别. 不会误判外键 (FOREIGN KEY) 等其它 IntegrityError.
            await db.rollback()
            error_msg = str(e).lower()
            is_dup_celery_task_id = (
                "celery_task_id" in error_msg
                and (
                    "duplicate" in error_msg  # MySQL
                    or "unique constraint" in error_msg  # SQLite / PostgreSQL
                )
            )
            if not is_dup_celery_task_id:
                # 其它完整性错误 (如外键) 不是 race, 直接抛
                raise
            logger.warning(
                "[job] race detected: celery_task_id=%s already exists, "
                "falling back to UPDATE existing (API resume path)",
                task_id,
            )
            # 重新查询 (API 端 UPDATE 已 commit, 这次一定能找到)
            existing = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if existing is None:
                # 极端情况: 两次操作之间行被删了, 抛原始错误
                logger.error(
                    "[job] race retry: celery_task_id=%s not found after rollback, "
                    "re-raising original IntegrityError",
                    task_id,
                )
                raise
            # 走 UPDATE 路径, 复用上面的 reset 逻辑 (inlined 避免双层嵌套)
            is_api_precreated = existing.message in (
                "等待 worker 启动...",
                "任务已入队, 等待 worker 启动...",
            )
            existing.state = "PROGRESS"
            existing.progress = 0.0
            existing.error = None
            if not is_api_precreated:
                existing.message = "Re-running (worker restart recovery)"
            existing.started_at = started_at
            existing.finished_at = None
            existing.duration_seconds = None
            existing.task_type = task_type
            existing.data_total = None
            existing.data_train = None
            existing.data_val = None
            existing.num_classes = None
            existing.class_names = None
            await db.commit()
            await db.refresh(existing)
            return existing.id
        await db.refresh(job)
        return job.id


def create_or_reset_job_sync(**kwargs) -> int:
    """同步包装: worker 进程直接调, 内部切到事件循环"""
    return _run_async(create_or_reset_job(**kwargs))


# ============== 2. 进度更新 (DB) ==============

async def update_job_progress(
    job_id: int,
    *,
    progress: float,
    message: Optional[str] = None,
    current_epoch: Optional[int] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """更新 TrainingJob 进度字段 (DB)

    与 push_history 配合: 单纯更新 progress/message/epoch,
    history 由 push_history 单独写库避免重复 IO.
    """
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob

    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(TrainingJob, job_id)
            if not job:
                return
            job.progress = float(progress)
            if message is not None:
                job.message = message
            if current_epoch is not None:
                job.current_epoch = current_epoch
            if history is not None:
                job.history = list(history)
            await db.commit()
    except Exception as e:
        # 写库失败不阻塞训练主流程
        logger.warning("update_job_progress failed for job %s: %s", job_id, e)


def update_job_progress_sync(**kwargs) -> None:
    """同步包装"""
    _run_async(update_job_progress(**kwargs))


# ============== 3. 训练历史推送 (Redis + DB) ==============

def push_history(
    task_id: str,
    history_buffer: List[Dict[str, Any]],
    *,
    job_id: Optional[int] = None,
    progress: Optional[float] = None,
    message: Optional[str] = None,
    current_epoch: Optional[int] = None,
    commit_db: bool = True,
) -> None:
    """训练历史曲线写入 Redis + DB

    双写 (Redis 优先, DB 兜底):
    1) Redis train:history:{task_id} (LIST) — 供前端 /training/history 端点 5s 轮询
    2) TrainingJob.history (DB) — Redis 失效兜底 + 训练后历史保留

    v3.1.0 Phase W4.2: Redis 写入从全量 STRING json.dumps 改为 RPUSH 增量.
    旧实现每 epoch 都 json.dumps 整个 history_buffer (随 epoch 增长线性变大).
    新实现只 RPUSH 最新 epoch 的 JSON, O(1) per epoch.
    读取端 (history.py) 用 LRANGE 0 -1 + 逐元素 json.loads 聚合.

    v3.5.0 Phase T7 #8: 合并 DB write
    - 传 commit_db=False 时, 跳过 update_job_progress_sync (DB 写)
      适用于 epoch_cb 场景: set_task_state 已合并写 (log + progress + current_epoch + history),
      push_history 只负责 RPUSH 到 Redis
    - 默认 commit_db=True 保持向后兼容 (旧调用方 / 外部脚本不受影响)

    兼容旧 worker 调用方式 (仅 task_id + history_buffer).
    """
    # ---- 1) Redis (增量 RPUSH) ----
    if history_buffer:
        try:
            key = f"train:history:{task_id}"
            # v3.1.0: 只推最新一个 epoch, 避免全量序列化
            latest = history_buffer[-1]
            redis_client.rpush(key, json.dumps(latest))
            # 设过期时间 (RPUSH 后重设, 避免列表无限增长)
            redis_client.expire(key, 86400)
        except Exception as e:
            logger.warning("push_history redis failed for %s: %s", task_id, e)

    # ---- 2) DB (job_id 存在时, Phase T7 #8: 可跳过) ----
    if commit_db and job_id is not None:
        update_job_progress_sync(
            job_id=job_id,
            progress=progress or 0.0,
            message=message,
            current_epoch=current_epoch,
            history=history_buffer,
        )
    # v3.5.0 Phase T7 方案 C: 写完 history 后 publish, 通知 SSE 端点立即推送
    # 这样 epoch 结束的曲线数据会立即出现在前端, 而不是等 1s 兜底
    try:
        from app.tasks.service.training_lifecycle_service.celery import publish_job_update
        publish_job_update(task_id)
    except Exception:
        pass


# ============== 4. sticky_meta 持久化 (数据集统计) ==============

async def persist_dataset_stats(
    task_id: str,
    extra: Dict[str, Any],
    *,
    job_id: Optional[int] = None,
) -> None:
    """把数据集统计 (data_total/data_train/data_val/num_classes/class_names) 写库

    触发条件: extra 含 data_total + num_classes 关键字 (避免每个 callback 都写)
    失败不抛: 写库失败不能让训练炸, 仅记日志

    v3.1.0 Phase W3.3: 优先用 job_id 主键查询 (db.get), 避免 celery_task_id 非索引查询.
    job_id 由调用方传入 (create_or_reset_job 返回值), 不传时回退到 celery_task_id.
    """
    try:
        data_total = extra.get("data_total")
        data_train = extra.get("data_train")
        data_val = extra.get("data_val")
        num_classes = extra.get("num_classes")
        class_names = extra.get("class_names")
        if data_total is None or num_classes is None:
            logger.info(
                "[stats] skip: data_total/num_classes missing for %s", task_id
            )
            return

        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.tasks.model.training_job import TrainingJob

        async with AsyncSessionLocal() as db:
            # v3.1.0 Phase W3.3: 优先主键查询 (走索引), 回退到 celery_task_id
            if job_id is not None:
                job = await db.get(TrainingJob, job_id)
            else:
                job = (await db.execute(
                    select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
                )).scalar_one_or_none()
            if not job:
                logger.info("[stats] warn: no TrainingJob row for %s", task_id)
                return
            job.data_total = int(data_total) if data_total is not None else None
            job.data_train = int(data_train) if data_train is not None else None
            job.data_val = int(data_val) if data_val is not None else None
            job.num_classes = int(num_classes) if num_classes is not None else None
            job.class_names = list(class_names) if class_names is not None else None
            await db.commit()
            logger.info(
                "[stats] OK: task=%s db_id=%s data_total=%s num_classes=%s",
                task_id, job.id, data_total, num_classes,
            )
    except Exception as e:
        logger.warning(
            "persist_dataset_stats failed for %s: %s: %s",
            task_id, type(e).__name__, e,
        )


def persist_dataset_stats_sync(
    task_id: str,
    extra: Dict[str, Any],
    *,
    job_id: Optional[int] = None,
) -> None:
    """同步包装"""
    _run_async(persist_dataset_stats(task_id, extra, job_id=job_id))


__all__ = [
    "create_or_reset_job",
    "create_or_reset_job_sync",
    "update_job_progress",
    "update_job_progress_sync",
    "push_history",
    "persist_dataset_stats",
    "persist_dataset_stats_sync",
]
