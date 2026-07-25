"""
image.preview.segmentation 模块 — 分割置信度预览
==============================================

**v3.0.0 Phase S3 拆分**: 从 image/preview.py 抽离
**职责**: 分割任务 (DeepLabV3+ / torchvision) 的置信度预览实现

**主要逻辑**:
- 解析 fine-tune ModelVersion
- 加载模型 (fine-tune 优先, 缺则走 torchvision 预训练)
  - fine-tune: 从磁盘读 state_dict 字节, 调 seg_predict.load_model 构造模型
  - pretrained: seg_predict.load_pretrained_torchvision (HuggingFace/网络下载)
- 推理: seg_predict.predict_to_mask_image_with_conf → {abs_path: (mask, max_softmax)}
- would_label: max_softmax ≥ 阈值

**S3 网络错误兜底**:
- 检测 WinError 10060 / connection / timeout / huggingface 关键字, 返回 503
- 提示用户设置 HF_HUB_OFFLINE=1 或预下载权重
"""
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.tasks.model.model_version import ModelVersion
from app.core.config import settings

# 复用 preview 包内的工具函数
from app.tasks.api.preview._utils import _resolve_finetune_model, _attach_model_meta


async def preview_segmentation(
    db: AsyncSession, dataset: Dataset, images: list,
    *,
    model_name: str, model_id: Optional[int],
    confidence_threshold: float, use_finetune: bool,
) -> dict:
    """分割任务: 走 torchvision / fine-tune, 按 max_softmax 判定 would_label"""
    dataset_id = dataset.id
    used_finetune = False
    mv: Optional[ModelVersion] = None

    from app.tasks.ml.segmentation import seg_predict

    if use_finetune:
        mv = await _resolve_finetune_model(db, dataset_id, model_id)
        if mv:
            if not Path(mv.file_path).exists():
                raise HTTPException(400, f"Model file missing on disk: {mv.file_path}. Please retrain.")
            try:
                # 复用 load_model(state_dict_bytes, ...) 思路: 直接读 weights 字节
                with open(mv.file_path, "rb") as f:
                    sd_bytes = f.read()
                model = seg_predict.load_model(
                    sd_bytes, backbone=mv.base_model or "deeplabv3_resnet50",
                    num_classes=mv.num_classes, device="cpu",
                )
            except Exception as e:
                raise HTTPException(500, f"Failed to load fine-tune segmentation model: {e}")
            used_finetune = True
        else:
            # 冷启动: 走 torchvision 预训练
            try:
                model = seg_predict.load_pretrained_torchvision(model_name, device="cpu")
            except ValueError as ve:
                raise HTTPException(400, str(ve))
            except Exception as e:
                err_msg = str(e)[:200]
                if any(k in err_msg.lower() for k in ["winerror 10060", "connection", "timeout", "huggingface"]):
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            f"Model '{model_name}' cannot be loaded: no internet/HuggingFace access "
                            f"({err_msg}). Please pre-download the weights or set HF_HUB_OFFLINE=1."
                        ),
                    )
                raise HTTPException(500, f"Failed to load pretrained segmentation model: {err_msg}")
            used_finetune = False
    else:
        # 严格模式: 有 fine-tune 时强制走 fine-tune
        mv_any = (await db.execute(
            select(ModelVersion.id).order_by(ModelVersion.id.desc()).limit(1)
        )).scalars().first()
        if mv_any is not None:
            raise HTTPException(
                400, "Project has fine-tune models. Preview must use fine-tune models. "
                     "Set use_finetune=True to use the active fine-tune model."
            )
        try:
            model = seg_predict.load_pretrained_torchvision(model_name, device="cpu")
        except ValueError as ve:
            raise HTTPException(400, str(ve))
        except Exception as e:
            err_msg = str(e)[:200]
            if any(k in err_msg.lower() for k in ["winerror 10060", "connection", "timeout", "huggingface"]):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Model '{model_name}' cannot be loaded: no internet/HuggingFace access "
                        f"({err_msg}). Please pre-download the weights or set HF_HUB_OFFLINE=1."
                        f"Please pre-download the weights or set HF_HUB_OFFLINE=1."
                    ),
                )
            raise HTTPException(500, f"Failed to load pretrained segmentation model: {err_msg}")
        used_finetune = False

    # 推理
    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    try:
        masks_with_conf = seg_predict.predict_to_mask_image_with_conf(
            model, image_paths, crop_size=256, device="cpu",
        )
    except Exception as e:
        raise HTTPException(500, f"Segmentation inference failed: {str(e)[:200]}")

    items: list = []
    would_label = 0
    need_human = 0
    for img in images:
        abs_path = str(storage_root / img.storage_path)
        if abs_path not in masks_with_conf:
            items.append({
                "image_id": img.id, "filename": img.filename,
                "thumb_url": f"/api/files/{img.id}/preview",
                "max_conf": 0.0, "would_label": False, "reason": "infer_failed",
            })
            need_human += 1
            continue
        _, max_softmax = masks_with_conf[abs_path]
        would = max_softmax >= confidence_threshold
        items.append({
            "image_id": img.id, "filename": img.filename,
            "thumb_url": f"/api/files/{img.id}/preview",
            "max_conf": round(max_softmax, 4),
            "would_label": would,
            "reason": "would_label" if would else "below_threshold",
        })
        if would:
            would_label += 1
        else:
            need_human += 1

    payload = {
        "items": items,
        "would_label": would_label, "need_human": need_human, "no_match": 0,
        "total": len(items), "threshold": confidence_threshold,
        "model_name": model_name,
        "task_type": "segmentation",
    }
    payload = _attach_model_meta(
        payload, used_finetune=used_finetune, mv=mv,
        fallback_pretrained_name=model_name,
    )
    return payload
