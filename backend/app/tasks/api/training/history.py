"""
training.history 模块 — 训练历史曲线数据
=========================================

**v3.0.0 Phase O 拆分**: 从 training.py 抽离
**职责**: 训练历史曲线 (loss/acc 折线图) 数据接口

**数据来源 (双源)**:
1. Redis train:history:{task_id} — worker 每个 epoch 写入
   v3.1.0: 写入格式从 STRING (全量 JSON) 改为 LIST (RPUSH 增量),
   读取时用 LRANGE 0 -1 + 逐元素 json.loads 聚合; 兼容旧 STRING 格式.
2. TrainingJob.history 字段 — detection/segmentation worker 写入 (YOLO/DeepLab
   不走 classification 的 Redis 写入路径)

Redis 命中则用 Redis; 否则回退查 DB.history, 保证三种任务曲线都能展示.
Redis 不可达时返回空历史（前端展示 "暂无历史曲线"），避免 500.

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
    history = []
    try:
        # v3.1.0 Phase W4.2: 兼容 LIST (增量 RPUSH) 和 STRING (旧全量) 两种格式
        key_type = redis_client.type(history_key)
        if key_type == b"list" or key_type == "list":
            # 新格式: LIST, 逐元素 json.loads
            raw_items = redis_client.lrange(history_key, 0, -1)
            for item in raw_items:
                try:
                    history.append(json.loads(item))
                except (ValueError, TypeError):
                    pass
        elif key_type == b"string" or key_type == "string":
            # 旧格式: STRING, 整体 json.loads
            raw = redis_client.get(history_key)
            if raw:
                try:
                    history = json.loads(raw)
                except (ValueError, TypeError):
                    history = []
        # key 不存在 (None) 时 history 保持空列表, 走 DB 回退
    except Exception as e:
        # Redis 不可达 / 超时 / 权限问题 → 记日志, 继续走 DB 回退
        _logger.warning(
            "redis_client.get(%s) failed: %s; fallback to DB history", history_key, e
        )

    # ---- DB 回退: Redis 无数据时, 查 TrainingJob.history (detection/segmentation) ----
    if not history:
        try:
            # 重新查 (避免 ORM 缓存导致 history 字段过期)
            row = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if row is not None and isinstance(row.history, list):
                history = row.history
        except Exception:
            # DB 也查不到, 返回空历史
            history = []
    return {"task_id": task_id, "history": history}
