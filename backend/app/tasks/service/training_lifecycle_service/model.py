"""
training_lifecycle_service.model — ModelVersion 创建 (训练成功时落盘)
====================================================================

**v3.0.0 Phase S5 拆分**: 从 training_lifecycle_service.py (580行) 抽离
**职责**:
- create_model_version / _sync — 创建 ModelVersion 行, 按 task_type 写入不同指标

**设计要点**:
- task_type=detection: 写 map_50 / map_50_95 / precision / recall + training_log
- task_type=segmentation: 写 miou / pixel_accuracy (兼容 best_miou/best_pix_acc 别名)
- task_type=classification: 不写额外指标 (在 classification worker 内手动 update)
- extra_fields: 额外字段 (预留扩展, segmentation worker 也用)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.utils.async_helpers import run_async_in_worker as _run_async

logger = logging.getLogger(__name__)


# ============== 8. ModelVersion 创建 ==============

async def create_model_version(
    *,
    name: str,
    base_model: str,
    dataset_id: int,
    task_type: str,
    num_classes: int,
    file_path: str,
    metrics: Dict[str, Any],
    history: List[Dict[str, Any]],
    is_active: bool = False,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> int:
    """创建 ModelVersion 行 (training 成功时)

    Args:
        metrics: 训练指标 (map_50/map_50_95/precision/recall/miou/pixel_accuracy)
        history: 训练历史曲线
        extra_fields: 额外字段 (segmentation 用 miou/pixel_accuracy)

    Returns:
        ModelVersion.id
    """
    from app.database import AsyncSessionLocal
    from app.tasks.model.model_version import ModelVersion

    mv_data: Dict[str, Any] = {
        "name": name,
        "base_model": base_model,
        "dataset_id": dataset_id,
        "task_type": task_type,
        "num_classes": num_classes,
        "file_path": file_path,
        "is_active": is_active,
    }
    # 训练指标 (按 task_type 兼容)
    if task_type == "detection":
        mv_data["map_50"] = metrics.get("map_50")
        mv_data["map_50_95"] = metrics.get("map_50_95")
        mv_data["precision"] = metrics.get("precision")
        mv_data["recall"] = metrics.get("recall")
        mv_data["training_log"] = {"history": history}
    elif task_type == "segmentation":
        mv_data["miou"] = metrics.get("best_miou") or metrics.get("miou")
        mv_data["pixel_accuracy"] = metrics.get("best_pix_acc") or metrics.get("pixel_accuracy")

    if extra_fields:
        mv_data.update(extra_fields)

    async with AsyncSessionLocal() as db:
        mv = ModelVersion(**mv_data)
        db.add(mv)
        await db.commit()
        await db.refresh(mv)
        return mv.id


def create_model_version_sync(**kwargs) -> int:
    """同步包装"""
    return _run_async(create_model_version(**kwargs))


__all__ = [
    "create_model_version",
    "create_model_version_sync",
]
