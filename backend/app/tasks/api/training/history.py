"""
training.history 模块 — 训练历史曲线数据
=========================================

**v3.0.0 Phase O 拆分**: 从 training.py 抽离
**职责**: 训练历史曲线 (loss/acc 折线图) 数据接口

**数据来源 (双源)**:
1. Redis train:history:{task_id} — classification worker 每个 epoch 写入
2. TrainingJob.history 字段 — detection/segmentation worker 写入 (YOLO/DeepLab
   不走 classification 的 Redis 写入路径)

Redis 命中则用 Redis; 否则回退查 DB.history, 保证三种任务曲线都能展示.
Redis 不可达时返回空历史（前端展示 "暂无历史曲线"），避免 500.
"""
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.redis import redis_client
from app.admin.model.user import User
from app.middleware.http.auth import get_user_optional_for_query
from app.tasks.model.training_job import TrainingJob

router = APIRouter()
_logger = logging.getLogger(__name__)


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
        _logger.warning(
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
