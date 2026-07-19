"""Pydantic Schemas: Detection (v2.0.0 目标检测)

约定:
- BBox 坐标: 归一化 0-1 (与 YOLO txt 一致)
- 创建/更新: BBoxCreate
- 响应: BBoxOut
- 列表: BBoxListOut
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class BBoxBase(BaseModel):
    """BBox 共有字段: 归一化坐标 0-1"""
    x_min: float = Field(ge=0.0, le=1.0, description="归一化 x_min (0-1)")
    y_min: float = Field(ge=0.0, le=1.0, description="归一化 y_min (0-1)")
    x_max: float = Field(ge=0.0, le=1.0, description="归一化 x_max (0-1)")
    y_max: float = Field(ge=0.0, le=1.0, description="归一化 y_max (0-1)")
    category_id: Optional[int] = Field(default=None, description="类别 id")
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="AI 推理置信度 (人工为 null)"
    )

    def validate_box(self) -> None:
        """兜底: x_max > x_min, y_max > y_min"""
        if self.x_max <= self.x_min:
            raise ValueError(f"x_max ({self.x_max}) 必须大于 x_min ({self.x_min})")
        if self.y_max <= self.y_min:
            raise ValueError(f"y_max ({self.y_max}) 必须大于 y_min ({self.y_min})")


class BBoxCreate(BBoxBase):
    """创建 / 更新单条 BBox 标注"""
    source: str = Field(default="human", description="ai / human / human_corrected")


class BBoxBatchCreate(BaseModel):
    """批量保存: 一次请求写多张图, 用于 AI 预标注结果批量入库"""
    items: List[BBoxCreate]


class BBoxOut(BBoxBase):
    id: int
    image_id: int
    source: str
    annotated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BBoxListOut(BaseModel):
    """单图所有 BBox 列表响应"""
    image_id: int
    items: List[BBoxOut] = []


class BBoxBatchSaveResult(BaseModel):
    """批量入库结果 (S2 占位, S3 接入 Celery 后用真实结果)"""
    success: bool
    received: int
    message: str = ""
    image_ids: List[int] = []


class DetectionTrainRequest(BaseModel):
    """启动检测训练请求

    与 classification TrainStartRequest 字段对齐, 增量字段:
    - imgsz: 训练输入尺寸 (默认 640, YOLOv8 标准)
    - iou_threshold: NMS IoU 阈值
    - conf_threshold: 推理置信度阈值
    """
    dataset_id: int
    base_model: str = "yolov8n"
    model_name: str = ""
    epochs: int = 10
    batch_size: int = 16
    learning_rate: float = 1e-3
    imgsz: int = 640
    iou_threshold: float = 0.7
    conf_threshold: float = 0.25
    pretrained_model_path: str = ""

    # 关掉 Pydantic v2 默认的 model_ 命名空间保护, 避免 model_name/model_id 警告
    model_config = ConfigDict(protected_namespaces=())


class DetectionTrainResponse(BaseModel):
    task_id: str
    celery_task_id: str
    job_id: int
    state: str
    message: str
