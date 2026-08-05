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

v3.0.0 Phase 4 重构:
- run_auto_annotate: 业务下沉到 AutoAnnotateService.run (122 行 → 18 行, -85%)
- get_task_status: 业务下沉到 AutoAnnotateService.get_async_status (35 行 → 7 行, -80%)
- run_segmentation_pretrained: 分割预标注业务下沉到 SegmentationService (Phase 4.4 待办)

v3.5.0 Phase T7 性能修复 #1:
- /auto-annotate/models 端点原返回 17 条硬编码模型元数据, 无任何缓存
- 每个用户每次进入 Training/Annotate 页都全量拉取 (一次进训练页就有 1 次)
- 修复: 模块级常量 _CACHED_MODELS_PAYLOAD + lru_cache 包装端点
  - 进程内第一次调用序列化 1 次, 后续 0 序列化
  - HTTP 层加 Cache-Control: public, max-age=3600 头, 浏览器/网关 1h 内不重发
"""
from typing import Optional
from functools import lru_cache
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.model.dataset import Dataset
from app.tasks.model.image import Image
from app.tasks.model.annotation_log import AnnotationLog
# v3.0.0 Phase 4: 业务编排下沉到 Service
from app.tasks.service.auto_annotate_service import AutoAnnotateService
# v3.3.0 P0: 权限校验工具
from app.tasks.service.permission_service import assert_can_access_dataset
# v3.4.1 P1: 推理路径解析 (适配 minio 后端)
from app.common.storage import resolve_inference_paths

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
    启动 AI 预标注任务 (v3.0.0 Phase 4: thin wrapper, 业务下沉到 AutoAnnotateService)

    - 小批量: 同步处理（< 50 张）
    - 大批量: 异步走 Celery（>= 50 张或 async_mode=True）
    - 严格模式: base model 输出必须落在项目预设类目内, 否则不标注
    - 全部业务规则 (sync/async 模式判断 / 模型加载 / 预测过滤 / 状态机 / 审计日志) 在 Service
    """
    result = await AutoAnnotateService.run(
        db,
        dataset_id=req.dataset_id,
        model_name=req.model_name,
        confidence_threshold=req.confidence_threshold,
        user_id=current_user.id,
        async_mode=req.async_mode,
    )
    return AutoAnnotateResponse(**result.to_dict())


@router.get("/status/{task_id}")
async def get_task_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    查询异步预标注任务进度 (v3.0.0 Phase 4: thin wrapper, 业务下沉到 AutoAnnotateService)

    v3.3.6-STATS-ISOLATION 修复 (P0-越权):
      - 之前: 任何登录用户可查询任意 task_id 进度 (含他人数据集)
      - 现在: 校验 task meta 中的 user_id/dataset_id, 仅 owner / team_member 可查询
      - 严格最小权限: super_admin 也不旁路
    """
    return await AutoAnnotateService.get_async_status(
        task_id, current_user=current_user, db=db
    )


@router.get("/models")
async def list_available_models(
    current_user: User = Depends(get_current_user),
    response = None,  # FastAPI 自动注入 Response (用默认 fastapi.Response)
):
    """
    列出系统支持的基础预训练模型 (业务方选型参考)

    按 task_type 字段分组:
    - classification: timm ImageNet 预训练 (整图级)
    - detection:     ultralytics YOLOv8 COCO 80 类 (目标级)
    - segmentation:  torchvision COCO 21 类 (像素级)

    架构说明:
    - 三个后端目录 (train.py / yolo_train.py / seg_train.py) 各自负责对应 task_type 的训练与推理
    - 该接口是「单一权威」: 标注工作台基础模型下拉 + 训练页参考都从这拉
    - 前端按 task_type 字段过滤显示, 避免出现"任务类型不匹配的基础模型"
    - task_type 必填, 不允许 null (缺省 classification)
    - recommended=True 是新手引导默认推荐项 (1-3 个)
    - description: 中文适用场景说明, 前端在 option 底部 + tooltip 展示 (v2.5.47 新增)

    v3.5.0 Phase T7 #1 优化:
    - 静态数据缓存 (lru_cache), 进程内仅序列化 1 次
    - HTTP 响应头 Cache-Control: public, max-age=3600 (浏览器/网关 1h 内零请求)
    - 业务逻辑与 #1 fix 一致: 仍按需鉴权, 但 payload 复用模块级 cache
    """
    from fastapi import Response  # 局部 import, 避免循环
    if response is None:
        response = Response()
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _get_models_payload()


@lru_cache(maxsize=1)
def _get_models_payload() -> dict:
    """v3.5.0 Phase T7 #1: 进程级缓存的模型清单 payload

    - 第一次调用: 拼装 17 条模型元数据 + JSON 序列化
    - 后续调用: 直接返回已序列化结果, 0 计算 / 0 IO
    - 数据是硬编码的, 部署版本一致即无需失效
    - 若需要刷新: 进程重启, 或调用 _get_models_payload.cache_clear()
    """
    return {
        "models": [
            # ----- classification: timm ImageNet -----
            {"name": "resnet18",              "params": "11.7M", "imagenet_top1": 70.6, "framework": "timm",       "task_type": "classification", "recommended": False,
             "description": "轻量级残差网络, 训练快、显存占用低, 适合中小数据集快速验证或 CPU/低端 GPU 部署"},
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
             "description": "YOLOv8 small, 11.2M 参数, 速度-精度平衡, 适合典型工业质检任务"},
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

    model_config = ConfigDict(protected_namespaces=())


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

    # v3.3.0 P0 修复: 必须校验写权限
    await assert_can_access_dataset(db, current_user, dataset, require_write=True)

    # 2. 加载 torchvision 预训练模型
    from app.tasks.ml.segmentation import seg_predict
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
    # v3.4.1 P1: 推理路径解析 (local 直返 / minio 临时文件)
    async with resolve_inference_paths(images) as image_paths:
        try:
            masks_with_conf = seg_predict.predict_to_mask_image_with_conf(
                model, image_paths, crop_size=req.crop_size, device=req.device,
            )
        except Exception as e:
            raise HTTPException(500, f"Inference failed: {str(e)[:200]}")

        # 5. upsert SegmentationMask + 写 status + 审计
        from app.annotation.model.segmentation_mask import SegmentationMask
        auto_labeled = 0
        for img, abs_path in zip(images, image_paths):
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
