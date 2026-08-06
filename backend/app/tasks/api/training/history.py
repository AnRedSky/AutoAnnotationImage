"""
training.history 模块 — 训练历史曲线数据
=========================================

**v3.0.0 Phase O 拆分**: 从 training.py 抽离
**职责**: 训练历史曲线 (loss/acc 折线图) 数据接口

**数据来源 (双源, v3.6.7 改为「数据更全的源优先」)**:
1. Redis train:history:{task_id} — worker 每个 epoch RPUSH 最新一条 (v3.1.0 优化: O(1) per epoch)
2. TrainingJob.history 字段 — worker 每次 epoch 整列表写 (v3.5.0 Phase T7 #8 合并写)
   - **关键**: mark_paused 也会写完整 history_buffer → DB 包含 pause 前所有 epoch
   - resume 时新 task_id 的 Redis 列表为空, DB 保留 pre-resume 数据
3. 跨源合并策略: 两条数据可能不一致 (resume 场景), 取**数据更全**的源返回
   - Redis 与 DB 同步增长 (新训练) → 任意, 等长
   - resume 场景: DB ≥ Redis (DB 必包含 pre-resume, Redis 只有 post-resume)
   - 终态: DB 必有完整数据 (mark_success 写), Redis 可能因 TTL (24h) 丢失

v3.3.6-STATS-ISOLATION 修复 (P0-越权):
  - 之前: 鉴权可选, 任何用户(包括匿名) 可访问任意 task_id 的训练历史
    (含 loss/acc 曲线 — 训练指标, 属于业务数据)
  - 现在: 必须登录, 且 task 对应 job 必须满足 owner / team_member
  - 与 /api/training/progress/{task_id} 对齐: 严格最小权限, 防止通过 task_id
    枚举全平台训练历史
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.redis import redis_client
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.model.training_job import TrainingJob
from app.tasks.service.permission_service import (
    assert_can_access_training_job,
    log_permission_denied,
)

router = APIRouter()
_logger = logging.getLogger(__name__)


@router.get("/history/{task_id}")
async def get_training_history(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    返回训练历史曲线数据（用于前端绘制 loss/acc 折线图）

    v3.3.6-STATS-ISOLATION (P0-越权修复):
      - 必须登录, 之前鉴权可选 (current_user: User | None) 任何人/匿名可访问
      - 必须校验 task 对应 job 的访问权 (owner / team_member)
      - 与 /api/training/progress/* 端点对齐, 防止通过 task_id 枚举全平台训练历史
        (训练历史是业务数据, 跨用户泄露违反最小权限原则)
    """
    # v3.3.6: 1) 鉴权 + 2) 任务存在性 + 3) 权限校验
    # 找不到对应 job 时 (auto_annotate 等) → 拒绝 (与 progress 端点一致)
    job = (await db.execute(
        select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
    )).scalar_one_or_none()
    if job is None:
        await log_permission_denied(
            db,
            current_user=current_user,
            resource_type="training_job",
            resource_id=None,
            endpoint=f"/api/training/history/{task_id}",
            reason="training_job not found in DB",
            detail={"task_id": task_id},
        )
        raise HTTPException(403, "无权限访问此训练历史")
    await assert_can_access_training_job(
        db, current_user, job.user_id, job.dataset_id
    )

    history_key = f"train:history:{task_id}"
    redis_history: list = []
    try:
        # v3.1.0 Phase W4.2: 兼容 LIST (增量 RPUSH) 和 STRING (旧全量) 两种格式
        key_type = redis_client.type(history_key)
        if key_type == b"list" or key_type == "list":
            # 新格式: LIST, 逐元素 json.loads
            raw_items = redis_client.lrange(history_key, 0, -1)
            for item in raw_items:
                try:
                    redis_history.append(json.loads(item))
                except (ValueError, TypeError):
                    pass
        elif key_type == b"string" or key_type == "string":
            # 旧格式: STRING, 整体 json.loads
            raw = redis_client.get(history_key)
            if raw:
                try:
                    redis_history = json.loads(raw)
                except (ValueError, TypeError):
                    redis_history = []
        # key 不存在 (None) 时 redis_history 保持空列表, 走 DB 回退
    except Exception as e:
        # Redis 不可达 / 超时 / 权限问题 → 记日志, 继续走 DB 回退
        _logger.warning(
            "redis_client.get(%s) failed: %s; fallback to DB history", history_key, e
        )
        redis_history = []

    # ---- DB 回退 + 跨源合并 (v3.6.7 修复 resume 场景曲线断裂) ----
    # v3.6.7 HOTFIX: 训练曲线数据连续性
    # - 场景: 暂停 → 恢复 → 详情页只显示 resume 后曲线, 缺失 pre-resume 数据
    # - 根因: push_history 只 RPUSH 最新 epoch, resume 时新 task_id 的 Redis
    #   列表从 0 开始, 仅有 post-resume epochs. mark_paused 写入的 pre-resume
    #   数据只在 DB 中, 旧逻辑 (Redis 非空就返回 Redis) 永远拿不到.
    # - 修复: 同时取 Redis 与 DB, 取**数据更全**的源.
    #   * Redis delta (post-resume only) — 训练中实时增长
    #   * DB 完整历史 (pre-resume + post-resume) — mark_paused + epoch_cb 写入
    #   * 训练中两者等长, 任意; resume 后 DB ≥ Redis, 用 DB
    #   * 终态 / 跨天: Redis 可能因 24h TTL 丢失, DB 仍保留
    db_history: list = []
    try:
        # 重新查 (避免 ORM 缓存导致 history 字段过期)
        row = (await db.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalar_one_or_none()
        if row is not None and isinstance(row.history, list):
            db_history = list(row.history)
    except Exception:
        # DB 查不到, db_history 保持空
        db_history = []

    # v3.6.7: 跨源选择 — 取数据更完整的源
    # - DB 与 Redis 等长: 训练中同步, 任意; 选 DB 保证权威性
    # - DB > Redis: resume 后必有, 用 DB (修复曲线断裂)
    # - DB < Redis: 异常情况 (DB 没及时写, worker 刚 RPUSH), 用 Redis (保实时性)
    # - 两者都空: 返回空 (前端展示"暂无历史")
    if len(db_history) >= len(redis_history):
        history = db_history
    else:
        history = redis_history
    return {"task_id": task_id, "history": history}
