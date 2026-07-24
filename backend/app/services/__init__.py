"""
Services Package
================
公共业务服务模块: 几何计算、AI 推理、文件存储、**业务编排 (Phase 3 新增)**.

**业务编排服务 (v3.0.0 Phase 3)**:
- JobStateService: 训练任务状态统一查询 (解决 4 处真相源)
- TrainingService: 训练任务启动/编排
- ImageService: 图片业务编排 (标注 / 状态 / AI 候选)
- DatasetService: 数据集业务编排 (含级联删除)
- ModelService: 模型版本业务编排 (含激活/失活)
- DetectionService: 目标检测业务编排 (BBox AI/人工)
- SegmentationService: 分割业务编排 (mask AI/人工)
- AnnotationService: 标注统一入口 (派发 3 任务)
- UserService: 用户业务编排
- StatsService: 统计业务
- AuthService: 认证业务

**工具服务 (Phase 1 已有)**:
- ai_service: timm 模型加载/推理
- storage_service: 文件存储
- bbox_service: BBox 几何计算
"""
from app.services.ai_service import ai_service, AIService
from app.services.storage_service import storage_service, StorageService
from app.services.bbox_service import (
    BBox,
    validate_normalized_bbox,
    normalized_to_pixels,
    pixels_to_normalized,
    iou,
    nms,
    class_wise_nms,
    bbox_from_dict,
    bbox_to_yolo_line,
    yolo_line_to_bbox,
)
# v3.0.0 Phase 3 业务编排服务 (5 核心)
from app.services.job_state_service import JobStateService, JobStateSnapshot
from app.services.training_service import TrainingService
from app.services.image_service import ImageService
from app.services.dataset_service import DatasetService
from app.services.model_service import ModelService
# v3.0.0 Phase 3 补充 (6 个扩展)
from app.services.detection_service import DetectionService
from app.services.segmentation_service import SegmentationService
from app.services.annotation_service import AnnotationService
from app.services.user_service import UserService
from app.services.stats_service import StatsService
from app.services.auth_service import AuthService

__all__ = [
    # 工具服务
    "ai_service", "AIService",
    "storage_service", "StorageService",
    "BBox",
    "validate_normalized_bbox",
    "normalized_to_pixels",
    "pixels_to_normalized",
    "iou",
    "nms",
    "class_wise_nms",
    "bbox_from_dict",
    "bbox_to_yolo_line",
    "yolo_line_to_bbox",
    # 业务编排服务 (5 核心)
    "JobStateService", "JobStateSnapshot",
    "TrainingService",
    "ImageService",
    "DatasetService",
    "ModelService",
    # 业务编排服务 (6 扩展)
    "DetectionService",
    "SegmentationService",
    "AnnotationService",
    "UserService",
    "StatsService",
    "AuthService",
]
