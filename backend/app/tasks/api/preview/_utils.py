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

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.model_version import ModelVersion


async def _resolve_finetune_model(
    db: AsyncSession, dataset_id: int, model_id: Optional[int],
) -> Optional[ModelVersion]:
    """解析 fine-tune ModelVersion (显式 id 优先, 否则取数据集激活, 仍无则 None)

    v3.4.1 P0 修复: 显式 model_id 时强制校验 mv.dataset_id == dataset_id.
    防止前端把别的数据集的 ModelVersion 误传到当前数据集测评,
    否则 _custom_label_map 会拿当前数据集类目去映射**别数据集训练**的 idx,
    推理输出几乎必然全 no_match, 用户看到 46/46 no_match 完全无法定位问题.
    """
    mv: Optional[ModelVersion] = None
    if model_id is not None:
        mv = (await db.execute(
            select(ModelVersion).where(ModelVersion.id == model_id)
        )).scalars().first()
        if mv is not None and mv.dataset_id != dataset_id:
            raise HTTPException(
                400,
                f"ModelVersion id={model_id} 不属于当前数据集 (dataset_id={dataset_id}). "
                f"请选择本数据集训练出的 fine-tune 模型.",
            )
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
    use_finetune: bool = False,
) -> dict:
    """把模型元信息统一塞到 payload (前端稳定依赖这些字段)

    `fallback_to_pretrained` 语义 (v3.4.0 修正):
      - 仅当 use_finetune=True 但 used_finetune=False (无 fine-tune 模型,
        自动回退到 timm) 时为 True;
      - 显式 use_finetune=False (用户主动选择预训练) 不算回退, 仍为 False.

    `warning` 字段:
      - 回退到预训练时附带中文提示, 让前端能弹"兜底"提示条;
      - 其余场景为 None.
    """
    fallback = (use_finetune is True) and (used_finetune is False)
    payload["used_finetune"] = used_finetune
    payload["finetune_name"] = mv.name if (used_finetune and mv is not None) else None
    payload["base_model"] = (
        mv.base_model if (used_finetune and mv is not None)
        else fallback_pretrained_name
    )
    payload["model_id"] = mv.id if (used_finetune and mv is not None) else None
    payload["fallback_to_pretrained"] = fallback
    payload["warning"] = (
        "项目暂无 fine-tune 模型, 已自动回退到预训练模型 (ImageNet)."
        if fallback else None
    )
    return payload
