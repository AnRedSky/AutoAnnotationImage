"""
Auto-Annotate API: AI 预标注独立接口
====================================
核心创新点接口 1: AI 自动标注
支持两种模式:
  - sync: 同步小批量（< 50 张）立即返回结果
  - async: 异步大批量（> 50 张）通过 Celery 后台跑

重要: base model (timm ImageNet) 的 top-K 输出会被过滤到只保留项目预设类目
       不在项目类目内的标签 (class_579 / tabby_cat 等) 不会写入 ai_prediction
       过滤后无匹配的图保持 pending, 不强制标为 ai_labeled
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.image import Image
from app.models.category import Category
from app.models.dataset import Dataset
from app.models.user import User
from app.models.annotation_log import AnnotationLog
from app.core.deps import get_current_user
from app.services import ai_service
from app.services.ai_service import filter_predictions_to_categories
from app.config import settings

router = APIRouter()


class AutoAnnotateRequest(BaseModel):
    dataset_id: int
    model_name: str = "efficientnet_b0"
    confidence_threshold: float = 0.6
    async_mode: bool = False  # True 走 Celery, False 走同步

    # 关掉 Pydantic v2 默认的 model_ 命名空间保护, 避免 model_name 警告
    model_config = ConfigDict(protected_namespaces=())


class AutoAnnotateResponse(BaseModel):
    total: int
    auto_labeled: int
    need_human: int
    no_match: int = 0  # base model 输出与项目类目无交集的图数 (保持 pending, 不标注)
    avg_confidence: float
    threshold: float
    task_id: Optional[str] = None
    mode: str  # "sync" | "async"


@router.post("/run", response_model=AutoAnnotateResponse)
async def run_auto_annotate(
    req: AutoAnnotateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    启动 AI 预标注任务
    - 小批量: 同步处理（< 50 张）
    - 大批量: 异步走 Celery（>= 50 张或 async_mode=True）
    - 严格模式: base model 输出必须落在项目预设类目内, 否则不标注
    """
    # 校验数据集
    dataset = await db.get(Dataset, req.dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 取项目预设类目 (用于过滤 base model 输出)
    cat_rows = (await db.execute(
        select(Category).where(Category.dataset_id == req.dataset_id)
    )).scalars().all()
    category_names = [c.name for c in cat_rows]
    if not category_names:
        raise HTTPException(
            400,
            "数据集没有预设类目, 请先添加类目 (Dataset -> 类别) 再启动 AI 预标注"
        )

    # 取待标注图片数
    result = await db.execute(
        select(Image).where(
            Image.dataset_id == req.dataset_id,
            Image.status == "pending",
        )
    )
    images = result.scalars().all()
    total = len(images)
    if total == 0:
        return AutoAnnotateResponse(
            total=0, auto_labeled=0, need_human=0, no_match=0,
            avg_confidence=0.0, threshold=req.confidence_threshold,
            mode="sync", task_id=None
        )

    # 决定同步还是异步
    use_async = req.async_mode or total >= 50
    if use_async:
        # 异步模式: 提交 Celery 任务 (把 category_names 一并传入, 避免 worker 二次查库)
        from app.workers.tasks import auto_annotate_task
        async_result = auto_annotate_task.delay(
            dataset_id=req.dataset_id,
            model_name=req.model_name,
            confidence_threshold=req.confidence_threshold,
            user_id=current_user.id,
            category_names=category_names,
        )
        return AutoAnnotateResponse(
            total=total, auto_labeled=0, need_human=total, no_match=0,
            avg_confidence=0.0, threshold=req.confidence_threshold,
            mode="async", task_id=async_result.id
        )

    # 同步模式: 直接推理
    if ai_service.current_model_name != req.model_name:
        try:
            await ai_service.load_pretrained(req.model_name)
        except Exception as e:
            err_msg = str(e)[:200]
            # 区分"无网络/无模型"和"运行时错误"
            if any(k in err_msg.lower() for k in ["winerror 10060", "connection", "timeout", "huggingface"]):
                raise HTTPException(
                    status_code=503,
                    detail=f"Model '{req.model_name}' cannot be loaded: no internet/HuggingFace access ({err_msg}). Please pre-download the model or set INFERENCE_DEVICE=cpu with local weights."
                )
            raise HTTPException(500, f"Failed to load model: {err_msg}")

    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    predictions = await ai_service.batch_predict(image_paths, top_k=5)

    # 关键: 把 base model 的 top-K 过滤到只含项目预设类目
    filtered_preds = filter_predictions_to_categories(predictions, category_names)

    # 类目名 -> id 映射 (用于 ai_predict 审计的 to_label_id)
    name_to_id = {c.name: c.id for c in cat_rows}

    auto_labeled = 0
    no_match = 0
    confs_for_avg: list = []
    for img, fp in zip(images, filtered_preds):
        if fp is None:
            # 与项目类目无交集: 不写入 ai_prediction, 保持 pending
            img.ai_prediction = None
            no_match += 1
        else:
            img.ai_prediction = fp
            confs_for_avg.append(fp["top1_conf"])
            if fp["top1_conf"] >= req.confidence_threshold:
                img.status = "ai_labeled"
                # 写 ai_predict 审计 (与人工 confirm/correct/reject 一并出现在历史流)
                top1_label_name = fp.get("top1")
                to_label_id = name_to_id.get(top1_label_name)
                db.add(AnnotationLog(
                    image_id=img.id,
                    user_id=current_user.id,
                    action="ai_predict",
                    from_label_id=img.final_label_id,  # AI 之前的 final (如有, 通常为 None)
                    to_label_id=to_label_id,
                    time_spent_ms=0,
                ))
                auto_labeled += 1
            # 否则保持 pending, 等待人工标注 (但 ai_prediction 仍存, UI 可看候选)

    await db.commit()
    avg_conf = sum(confs_for_avg) / max(len(confs_for_avg), 1)

    return AutoAnnotateResponse(
        total=total, auto_labeled=auto_labeled, need_human=total - auto_labeled,
        no_match=no_match,
        avg_confidence=round(avg_conf, 4), threshold=req.confidence_threshold,
        mode="sync", task_id=None
    )


@router.get("/status/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    查询异步预标注任务进度
    """
    from celery.result import AsyncResult
    # 安全读取 state/info, 避免在 backend 不可用或 task 不存在时抛错
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

    return {
        "task_id": task_id,
        "state": state,
        "progress": info.get("progress", 0),
        "message": info.get("msg", ""),
        "total": info.get("total", 0),
        "auto_labeled": info.get("auto_labeled", 0),
    }


@router.get("/models")
async def list_available_models(current_user: User = Depends(get_current_user)):
    """
    列出系统支持的 timm 模型（论文核心实验用）
    framework: 模型来源框架（统一为 timm，便于前端展示标注）
    """
    return {
        "models": [
            {"name": "resnet50",              "params": "25.6M", "imagenet_top1": 76.1, "framework": "timm", "recommended": True},
            {"name": "efficientnet_b0",       "params": "5.3M",  "imagenet_top1": 77.1, "framework": "timm", "recommended": True},
            {"name": "convnext_tiny",         "params": "28.6M", "imagenet_top1": 82.1, "framework": "timm", "recommended": True},
            {"name": "mobilenetv3_small",     "params": "2.5M",  "imagenet_top1": 67.5, "framework": "timm", "recommended": False},
            {"name": "vit_small_patch16_224", "params": "22.1M", "imagenet_top1": 78.7, "framework": "timm", "recommended": False},
        ]
    }
