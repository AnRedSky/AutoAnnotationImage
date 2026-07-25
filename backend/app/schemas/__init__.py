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
from app.common.enums import (
    TaskType,
    TASK_TYPE_VALUES,
    TASK_TYPE_DEFAULT_BASE_MODEL,
    AnnotationSource,
    ANNOTATION_SOURCE_VALUES,
    DetectionTrainState,
    is_valid_task_type,
    normalize_task_type,
)
# v3.0.0 Phase 4 新增: 7 个新 schema
# 注: UserOut / ModelVersionOut 与 auth/training 已有, 各自从原模块导入, 不重复
from app.schemas.user import (
    UserBase,
    UserListOut,
    UserUpdateRequest,
    UserChangePasswordRequest,
)
from app.schemas.annotation import (
    AnnotationActionRequest,
    AnnotationActionResponse,
    BBoxAnnotationIn,
    SegmentationMaskIn,
)
from app.schemas.model import (
    ModelVersionListOut,
    ModelActivationRequest,
    ModelActivationResponse,
)
from app.schemas.stats import (
    GlobalOverview,
    DatasetOverview,
    CategoryStat,
    CategoryStatListOut,
)
from app.schemas.export import (
    ExportRequest,
    ExportResponse,
    ModelDownloadResponse,
)
from app.schemas.auto_annotate import (
    AutoAnnotateRequest,
    AutoAnnotateResponse,
    AutoLabelRequest,
    AutoLabelResponse,
    AvailableModelOut,
    AvailableModelsResponse,
)
from app.schemas.common import (
    ErrorDetail,
    ErrorResponse,
    SuccessResponse,
    PaginationRequest,
    PaginatedResponse,
    BatchOperationResult,
)
