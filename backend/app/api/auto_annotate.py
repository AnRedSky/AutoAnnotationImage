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
from pydantic import BaseModel, ConfigDict, Field
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
    列出系统支持的基础预训练模型 (论文核心实验用)

    按 task_type 字段分组:
    - classification: timm ImageNet 预训练 (整图级)
    - detection:     ultralytics YOLOv8 COCO 80 类 (目标级)
    - segmentation:  torchvision COCO 21 类 (像素级)

    架构说明:
    - 三个后端目录 (train.py / yolo_train.py / seg_train.py) 各自负责对应 task_type 的训练与推理
    - 该接口是「单一权威」: 标注工作台基础模型下拉 + 训练页参考都从这拉
    - 前端按 task_type 字段过滤显示, 避免出现"任务类型不匹配的基础模型"
    - task_type 必填, 不允许 null (缺省 classification)
    - recommended=True 是论文 demo 默认推荐项 (1-3 个)
    - description: 中文适用场景说明, 前端在 option 底部 + tooltip 展示 (v2.5.47 新增)
    """
    return {
        "models": [
            # ----- classification: timm ImageNet -----
            {"name": "resnet18",              "params": "11.7M", "imagenet_top1": 70.6, "framework": "timm",       "task_type": "classification", "recommended": False,
             "description": "轻量级残差网络, 训练快、显存占用低, 适合中小数据集快速实验或 CPU/低端 GPU 部署"},
            {"name": "resnet50",              "params": "25.6M", "imagenet_top1": 76.1, "framework": "timm",       "task_type": "classification", "recommended": True,
             "description": "经典深度残差网络, 特征表达力强, 适合中等规模数据集与追求高精度的训练场景"},
            {"name": "efficientnet_b0",       "params": "5.3M",  "imagenet_top1": 77.1, "framework": "timm",       "task_type": "classification", "recommended": True,
             "description": "复合缩放轻量网络, 速度与精度平衡, 适合移动端、实时推理或算力受限场景"},
            {"name": "efficientnet_b3",       "params": "12.0M", "imagenet_top1": 81.6, "framework": "timm",       "task_type": "classification", "recommended": False,
             "description": "B0 的精度升级版, 中等规模数据下表现更稳, 适合精度-速度折中的工业分类任务"},
            {"name": "mobilenetv3_large_100", "params": "5.5M",  "imagenet_top1": 75.0, "framework": "timm",       "task_type": "classification", "recommended": False,
             "description": "移动端优化网络, 延迟极低, 适合边缘设备、嵌入式或 Web 前端推理部署"},
            {"name": "convnext_tiny",         "params": "28.6M", "imagenet_top1": 82.1, "framework": "timm",       "task_type": "classification", "recommended": True,
             "description": "现代化纯卷积架构, 精度可比 Transformer, 适合数据量充足、追求高精度的训练任务"},
            {"name": "vit_small_patch16_224", "params": "22.1M", "imagenet_top1": 78.7, "framework": "timm",       "task_type": "classification", "recommended": False,
             "description": "小型 Vision Transformer, 224 输入, 注意力机制捕获全局依赖, 适合中等规模数据集与精度敏感任务"},
            # ----- detection: ultralytics YOLOv8 COCO 80 类 -----
            {"name": "yolov8n", "params": "3.2M",  "coco_mAP50": 37.3, "framework": "ultralytics", "task_type": "detection",     "recommended": True,
             "description": "YOLOv8 nano, 3.2M 参数, 速度极快, 适合移动端部署、实时检测或算力受限的工业场景"},
            {"name": "yolov8s", "params": "11.2M", "coco_mAP50": 44.9, "framework": "ultralytics", "task_type": "detection",     "recommended": True,
             "description": "YOLOv8 small, 11.2M 参数, 速度-精度平衡, 适合论文 demo 与一般工业质检任务"},
            {"name": "yolov8m", "params": "25.9M", "coco_mAP50": 50.2, "framework": "ultralytics", "task_type": "detection",     "recommended": False,
             "description": "YOLOv8 medium, 25.9M 参数, 中等规模, 适合数据量充足且追求较高精度的检测任务"},
            {"name": "yolov8l", "params": "43.7M", "coco_mAP50": 52.9, "framework": "ultralytics", "task_type": "detection",     "recommended": False,
             "description": "YOLOv8 large, 43.7M 参数, 高精度, 适合复杂场景检测 (如小目标、密集目标)"},
            {"name": "yolov8x", "params": "68.2M", "coco_mAP50": 53.9, "framework": "ultralytics", "task_type": "detection",     "recommended": False,
             "description": "YOLOv8 xlarge, 68.2M 参数, 极致精度, 需要大显存 GPU (≥ 16GB), 适合离线批检测或竞赛级精度需求"},
            # ----- segmentation: torchvision COCO 21 类 -----
            {"name": "fcn_resnet50",          "params": "32.9M", "coco_mIoU": 60.5, "framework": "torchvision", "task_type": "segmentation",  "recommended": False,
             "description": "FCN + ResNet50 骨干, 32.9M 参数, 经典全卷积分割, 速度快, 适合算力受限或实时分割场景"},
            {"name": "deeplabv3_resnet50",    "params": "39.6M", "coco_mIoU": 66.4, "framework": "torchvision", "task_type": "segmentation",  "recommended": True,
             "description": "DeepLabV3 + ResNet50 骨干, 39.6M 参数, ASPP 多尺度模块, 速度-精度平衡, 适合一般语义分割任务"},
            {"name": "deeplabv3_resnet101",   "params": "58.7M", "coco_mIoU": 67.4, "framework": "torchvision", "task_type": "segmentation",  "recommended": True,
             "description": "DeepLabV3 + ResNet101 骨干, 58.7M 参数, 深层特征, 适合精度优先的复杂场景分割"},
        ]
    }


# v2.5.46: 分割任务「基础预标注」请求/响应 schema
# 区别于检测/分割 fine-tune 的 Celery 异步路径 (POST /api/segmentation/auto-annotate),
# 该端点是**同步**的: 数据集小 + COCO 预训练推理快, 同步即可 (auto_annotate.py 的
# classification 路径也是同步的, 保持架构一致)
class RunSegmentationPretrainedRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    dataset_id: int
    model_name: str = "deeplabv3_resnet50"
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    crop_size: int = Field(default=256, ge=64, le=1024)
    device: str = "cpu"
    # 是否覆盖已有人工标注的 mask (与 /api/segmentation/auto-annotate 语义一致)
    overwrite_existing: bool = False


class RunSegmentationPretrainedResponse(BaseModel):
    total: int
    auto_labeled: int
    need_human: int
    model_name: str
    threshold: float
    mode: str = "sync"


@router.post(
    "/run-segmentation-pretrained",
    response_model=RunSegmentationPretrainedResponse,
)
async def run_segmentation_pretrained(
    req: RunSegmentationPretrainedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    v2.5.46: 分割任务「基础预标注」同步端点
    - 加载 torchvision 预训练 (COCO 21 类) 分割模型, 对 pending 图同步跑推理
    - max_softmax >= confidence_threshold → image.status = 'ai_labeled'
      否则保留 'pending', 但 SegmentationMask 仍写入 (source='ai') 供后续人工精修
    - overwrite_existing=False: 已存在 mask 的图直接跳过 (避免覆盖人工标注)
    - 网络错误 (HF 不可达 / OSError) → 503, 文案与 classification 路径 (L122-127) 对齐
    """
    # 1. 数据集校验
    dataset = await db.get(Dataset, req.dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if (dataset.task_type or "classification") != "segmentation":
        raise HTTPException(
            400,
            f"该接口仅服务于分割任务数据集, 当前 dataset.task_type='{dataset.task_type}'",
        )

    # 2. 加载 torchvision 预训练模型
    from app.ml.segmentation import seg_predict
    try:
        model = seg_predict.load_pretrained_torchvision(
            req.model_name, device=req.device,
        )
    except ValueError as ve:
        # 不支持的 backbone → 400
        raise HTTPException(400, str(ve))
    except Exception as e:
        # 网络/权重下载等错误 → 503, 与 auto_annotate.py:122-127 文案一致
        err_msg = str(e)[:200]
        if any(k in err_msg.lower() for k in ["winerror 10060", "connection", "timeout", "huggingface"]):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Model '{req.model_name}' cannot be loaded: no internet/HuggingFace access "
                    f"({err_msg}). Please pre-download the weights or set HF_HUB_OFFLINE=1."
                ),
            )
        raise HTTPException(500, f"Failed to load pretrained model: {err_msg}")

    # 3. 取候选图片: 优先 pending; overwrite_existing=True 时取全部非已锁定状态
    if req.overwrite_existing:
        result = await db.execute(
            select(Image).where(
                Image.dataset_id == req.dataset_id,
                Image.status.in_(["pending", "ai_labeled"]),
            )
        )
    else:
        result = await db.execute(
            select(Image).where(
                Image.dataset_id == req.dataset_id,
                Image.status == "pending",
            )
        )
    images = result.scalars().all()
    total = len(images)
    if total == 0:
        return RunSegmentationPretrainedResponse(
            total=0, auto_labeled=0, need_human=0,
            model_name=req.model_name, threshold=req.confidence_threshold,
        )

    # 4. 同步批量推理
    storage_root = settings.UPLOAD_DIR
    image_paths = [str(storage_root / img.storage_path) for img in images]
    try:
        masks_with_conf = seg_predict.predict_to_mask_image_with_conf(
            model, image_paths, crop_size=req.crop_size, device=req.device,
        )
    except Exception as e:
        raise HTTPException(500, f"Inference failed: {str(e)[:200]}")

    # 5. upsert SegmentationMask + 写 status + 审计
    from app.models.segmentation_mask import SegmentationMask
    auto_labeled = 0
    for img in images:
        abs_path = str(storage_root / img.storage_path)
        if abs_path not in masks_with_conf:
            # 单图推理失败 (e.g. 损坏的图像) → 跳过, 不影响整体
            continue
        pil_mask, max_softmax = masks_with_conf[abs_path]

        # overwrite_existing=False 时: 已存在 mask 直接跳过
        existing = (await db.execute(
            select(SegmentationMask).where(SegmentationMask.image_id == img.id)
        )).scalars().first()
        if existing and not req.overwrite_existing:
            continue

        # 持久化 mask 到 storage_service
        mask_path = seg_predict.save_mask_pil(pil_mask, req.dataset_id, img.id)

        if existing:
            # 覆盖: 删旧 + 写新
            await db.delete(existing)
            await db.flush()
        new_mask = SegmentationMask(
            image_id=img.id,
            mask_path=mask_path,
            source="ai",
            annotated_by=current_user.id,
        )
        db.add(new_mask)

        # 判定是否落标 (max_softmax 是 softmax 输出最大值, ∈ [0, 1])
        would_label = max_softmax >= req.confidence_threshold
        if would_label:
            img.status = "ai_labeled"
            db.add(AnnotationLog(
                image_id=img.id,
                user_id=current_user.id,
                action="ai_predict",
                from_label_id=img.final_label_id,
                to_label_id=None,  # 分割任务无单 label_id
                time_spent_ms=0,
            ))
            auto_labeled += 1
        # 否则保持 pending (mask 已写入, 人工可继续精修)

    await db.commit()

    return RunSegmentationPretrainedResponse(
        total=total,
        auto_labeled=auto_labeled,
        need_human=total - auto_labeled,
        model_name=req.model_name,
        threshold=req.confidence_threshold,
    )
