"""Pydantic Schemas Package"""
from app.schemas.auth import RegisterRequest, TokenResponse, UserOut
from app.schemas.dataset import (
    CategoryOut,
    CategoryCreate,
    DatasetBase,
    DatasetCreate,
    DatasetOut,
)
from app.schemas.image import (
    ImageOut,
    ImageListOut,
    AnnotationSaveRequest,
    AnnotationSaveResponse,
    AnnotationLogOut,
)
from app.schemas.training import (
    TrainStartRequest,
    TrainStartResponse,
    TrainStatusResponse,
    ModelVersionOut,
    TrainingJobOut,
)
# v2.0.0: 检测 / 分割
from app.schemas.detection import (
    BBoxBase,
    BBoxCreate,
    BBoxBatchCreate,
    BBoxOut,
    BBoxListOut,
    DetectionTrainRequest,
    DetectionTrainResponse,
)
from app.schemas.segmentation import (
    SegmentationMaskBase,
    SegmentationMaskCreate,
    SegmentationMaskOut,
    SegmentationTrainRequest,
    SegmentationTrainResponse,
)
from app.schemas.enums import (
    TaskType,
    TASK_TYPE_VALUES,
    TASK_TYPE_DEFAULT_BASE_MODEL,
    AnnotationSource,
    ANNOTATION_SOURCE_VALUES,
    DetectionTrainState,
    is_valid_task_type,
    normalize_task_type,
)
