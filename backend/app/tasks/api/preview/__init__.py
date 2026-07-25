"""
preview API Package (v3.0.0 Phase S3 拆分)
==========================================

**职责**: 置信度预览 (非破坏性测评, 不写库/不写审计)

**目录结构** (拆分自原 image/preview.py, 577 行 → 4 子模块):
- __init__.py           POST /preview-confidence 路由 + PreviewConfidenceRequest Schema + 三路分派
- _utils.py             2 个公共工具: _resolve_finetune_model / _attach_model_meta
- classification.py     分类预览 (top-1 + would_label)
- detection.py          检测预览 (bbox 列表 + would_label)
- segmentation.py       分割预览 (mask max_softmax + would_label)

**v3.0.0 Phase S3 拆分**:
- 从原 image/preview.py (577 行) 拆出 3 个 task_type 实现 + 1 个工具模块
- 3 个分派函数从 _preview_* 改为 preview_*, 公共前置下划线移除 (因为现在它们是公开 API)
- 旧 image/preview.py 文件已删除, image 包内 preview 仍作为子 router 暴露
- 注意: preview 包是独立模块 (app/tasks/api/preview/), 不再嵌在 image/ 下, 减少耦合
  - 但 image/__init__.py 仍 include preview_router, 保持 /api/images/preview-confidence 路径不变

**对外接口 (完全向后兼容)**: 1 个路由保持不变
- POST /api/images/preview-confidence    置信度预览 (按 dataset.task_type 三路分派)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user

from app.tasks.api.preview.classification import preview_classification
from app.tasks.api.preview.detection import preview_detection
from app.tasks.api.preview.segmentation import preview_segmentation


router = APIRouter()


# ---------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------
class PreviewConfidenceRequest(BaseModel):
    """
    POST /api/images/preview-confidence 请求体
    非破坏性测评: 对指定图片跑模型, 返回 top-1 置信度及在当前阈值下是否会被自动标注
    """
    model_config = ConfigDict(protected_namespaces=())  # 允许 model_name/model_id 字段
    dataset_id: int = Field(..., description="数据集 id")
    image_ids: List[int] = Field(..., description="要测评的图片 id 列表")
    model_name: Optional[str] = Field(
        default="efficientnet_b0",
        description="timm 模型名 (仅 use_finetune=False 时使用)"
    )
    model_id: Optional[int] = Field(
        default=None,
        description="指定 fine-tune ModelVersion.id; 缺省=激活的"
    )
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    use_finetune: bool = Field(default=True, description="True=用 fine-tune; False=用 timm ImageNet")


# ---------------------------------------------------------------------
# 路由入口: 三路分派
# ---------------------------------------------------------------------
@router.post("/preview-confidence")
async def preview_confidence(
    req: PreviewConfidenceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    **非破坏性** 测评接口: 对指定图片跑模型, 返回 top-1 置信度及在当前阈值下是否会被自动标注

    v2.5.46 改造: 按 dataset.task_type 三路分派
    - classification: 单标签 top-1 预览 (原行为, 保留)
    - detection:     bbox 列表预览, 按"是否含 ≥ 阈值 bbox"判定 would_label
    - segmentation:  mask 预览, 按 max_softmax 是否 ≥ 阈值判定 would_label

    用途: 在点击「启动 AI 预标注」之前, 让用户先看到「如果现在跑批量预标注, 哪些图会被标、哪些会留在待标注」
    - **完全不改数据库** (不写 ai_prediction, 不改 status)
    - 不写 AnnotationLog 审计
    - 用户可反复调整阈值/模型预览, 选定后再点击批量预标注
    """
    dataset_id = req.dataset_id
    image_ids = req.image_ids
    model_name = req.model_name or "efficientnet_b0"
    model_id = req.model_id
    confidence_threshold = req.confidence_threshold
    use_finetune = req.use_finetune

    if not image_ids:
        return {
            "items": [],
            "would_label": 0,
            "need_human": 0,
            "no_match": 0,
            "threshold": confidence_threshold,
            "model_name": None,
            "used_finetune": False,
        }

    # 1. 取图片 (限定数据集 + 给定 id 集合)
    result = await db.execute(
        select(Image).where(
            Image.dataset_id == dataset_id,
            Image.id.in_(image_ids),
        )
    )
    images = result.scalars().all()
    if not images:
        return {
            "items": [],
            "would_label": 0,
            "need_human": 0,
            "no_match": 0,
            "total": 0,
            "threshold": confidence_threshold,
            "model_name": None,
            "used_finetune": False,
            "finetune_name": None,
            "base_model": None,
            "model_id": None,
            "fallback_to_pretrained": False,
        }

    # v2.5.46: 按 dataset.task_type 三路分派
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    task_type = (dataset.task_type or "classification").lower()

    if task_type == "detection":
        return await preview_detection(
            db, dataset, images,
            model_name=model_name, model_id=model_id,
            confidence_threshold=confidence_threshold, use_finetune=use_finetune,
        )
    if task_type == "segmentation":
        return await preview_segmentation(
            db, dataset, images,
            model_name=model_name, model_id=model_id,
            confidence_threshold=confidence_threshold, use_finetune=use_finetune,
        )
    # default: classification (沿用原实现)
    return await preview_classification(
        db, dataset, images,
        model_name=model_name, model_id=model_id,
        confidence_threshold=confidence_threshold, use_finetune=use_finetune,
    )


__all__ = ["router", "PreviewConfidenceRequest", "preview_classification", "preview_detection", "preview_segmentation"]
