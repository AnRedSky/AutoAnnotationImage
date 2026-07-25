"""
image.preview._utils 模块 — 预览接口的公共工具
==============================================

**v3.0.0 Phase S3 拆分**: 从 image/preview.py 抽离
**职责**: 3 个 task_type 分派函数共用的工具

**工具函数**:
- `_resolve_finetune_model`: 解析 fine-tune ModelVersion (显式 id 优先 → 激活模型 → None)
- `_attach_model_meta`: 把模型元信息统一塞到 payload (前端稳定依赖)
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.model_version import ModelVersion


async def _resolve_finetune_model(
    db: AsyncSession, dataset_id: int, model_id: Optional[int],
) -> Optional[ModelVersion]:
    """解析 fine-tune ModelVersion (显式 id 优先, 否则取数据集激活, 仍无则 None)"""
    mv: Optional[ModelVersion] = None
    if model_id is not None:
        mv = (await db.execute(
            select(ModelVersion).where(ModelVersion.id == model_id)
        )).scalars().first()
    if mv is None:
        mv = (await db.execute(
            select(ModelVersion)
            .where(ModelVersion.is_active == True, ModelVersion.dataset_id == dataset_id)  # noqa: E712
            .order_by(ModelVersion.id.desc()).limit(1)
        )).scalars().first()
    return mv


def _attach_model_meta(
    payload: dict, *,
    used_finetune: bool, mv: Optional[ModelVersion],
    fallback_pretrained_name: str,
) -> dict:
    """把模型元信息统一塞到 payload (前端稳定依赖这些字段)"""
    payload["used_finetune"] = used_finetune
    payload["finetune_name"] = mv.name if (used_finetune and mv is not None) else None
    payload["base_model"] = (
        mv.base_model if (used_finetune and mv is not None)
        else fallback_pretrained_name
    )
    payload["model_id"] = mv.id if (used_finetune and mv is not None) else None
    payload["fallback_to_pretrained"] = False
    payload["warning"] = None
    return payload
