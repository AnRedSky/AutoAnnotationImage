"""
Tasks Application — 业务应用 3/4
=================================

**职责** (最核心最复杂):
- 数据集 (Dataset) CRUD + 级联删除
- 图像 (Image) 上传/查询/状态管理
- 类别 (Category) 维护
- 训练任务 (TrainingJob) 启动/查询/取消
- 模型版本 (ModelVersion) 激活/失活/查询
- 目标检测 (Detection) BBox 标注 + 训练
- 图像分割 (Segmentation) Mask 标注 + 训练
- AI 预标注 (AutoAnnotate) 同步/异步推理
- 标注导出 (Export) YOLO/COCO
- 文件服务 (Files) 上传/下载

**应用特征**:
- 名称: tasks
- 版本: 3.0.0
- API 前缀: /api/{datasets,images,training,models,auto-annotate,export,detection,segmentation,files}
- 依赖: app.common, app.database, app.middleware, app.utils, app.admin.model.User

**目录约定**:
- api/        路由层 (9 个子模块)
- service/    业务编排 (10+ 个服务)
- model/      ORM 模型 (8 个表)
- schema/     Pydantic DTO (8 个子模块)
- repository/ 复杂查询 (5+ 个查询文件)
- ml/         ML 业务逻辑 (classification / detection / segmentation)
- workers/    Celery 任务入口 (委托 service)
- events.py   领域事件定义 (TaskStarted, TaskCompleted 等)

**重要约束**:
- ML 模块和 workers 都依赖 tasks.model, 不允许跨应用访问其他 model
- 与 admin/auth 的通信全部走 EventBus, 禁止直接 import
- 文件存储 (MinIO/Local) 在 service 层使用, 通过 storage_service 抽象

**v3.0.0 Stage 2 新增**: 多应用架构骨架
"""
from fastapi import APIRouter

from app.common.interfaces import AppInterface
from app.registry import AppRegistry


class TasksApp(AppInterface):
    """Tasks 应用 — 训练任务/检测/分割/自动标注/导出/数据集/图像"""

    @property
    def name(self) -> str:
        return "tasks"

    @property
    def version(self) -> str:
        return "3.0.0"

    @property
    def router(self) -> APIRouter:
        return _tasks_router

    def register_events(self) -> list[str]:
        return [
            "task.started",
            "task.progress",
            "task.completed",
            "task.failed",
            "task.paused",
            "model.trained",
            "model.activated",
            "model.deleted",
            "dataset.created",
            "dataset.deleted",
            "image.uploaded",
            "image.deleted",
            "auto_annotation.done",
        ]


# ============== 根路由 (Stage 2.5 之后会聚合 9 个子模块) ==============
_tasks_router = APIRouter()

# Stage 2.5 完成后, 这里会类似:
# from app.tasks.api.dataset import router as dataset_router
# from app.tasks.api.image import router as image_router
# from app.tasks.api.training import router as training_router
# from app.tasks.api.model import router as model_router
# from app.tasks.api.auto_annotate import router as auto_annotate_router
# from app.tasks.api.export import router as export_router
# from app.tasks.api.detection import router as detection_router
# from app.tasks.api.segmentation import router as segmentation_router
# from app.tasks.api.files import router as files_router
#
# _tasks_router.include_router(dataset_router, prefix="/datasets", tags=["数据集管理"])
# _tasks_router.include_router(image_router, prefix="/images", tags=["图像管理"])
# _tasks_router.include_router(training_router, prefix="/training", tags=["模型训练"])
# _tasks_router.include_router(model_router, prefix="/models", tags=["模型管理"])
# _tasks_router.include_router(auto_annotate_router, prefix="/auto-annotate", tags=["AI预标注"])
# _tasks_router.include_router(export_router, prefix="/export", tags=["标注导出"])
# _tasks_router.include_router(detection_router, prefix="/detection", tags=["目标检测"])
# _tasks_router.include_router(segmentation_router, prefix="/segmentation", tags=["图像分割"])
# _tasks_router.include_router(files_router, prefix="/files", tags=["文件服务"])


# ============== 应用注册 ==============
AppRegistry.register(TasksApp())


__all__ = ["TasksApp", "tasks_app"]

# 便捷引用: app.tasks.tasks_app
tasks_app = TasksApp()
