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
**v3.0.0 Stage 2.7**: get_routes() 返回 9 个路由条目
"""
from fastapi import APIRouter

from app.common.interfaces import AppInterface, RouteEntry
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

    def get_routes(self) -> list[RouteEntry]:
        """返回 9 个路由条目: tasks 应用的全部 API"""
        from app.tasks.api import (
            dataset as dataset_api,
            image as image_api,
            training as training_api,
            model as model_api,
            auto_annotate as auto_annotate_api,
            export as export_api,
            detection as detection_api,
            segmentation as segmentation_api,
            files as files_api,
        )
        return [
            RouteEntry(dataset_api, "/api/datasets", ["数据集管理"]),
            RouteEntry(image_api, "/api/images", ["图像管理"]),
            RouteEntry(training_api, "/api/training", ["模型训练"]),
            RouteEntry(model_api, "/api/models", ["模型管理"]),
            RouteEntry(auto_annotate_api, "/api/auto-annotate", ["AI预标注"]),
            RouteEntry(export_api, "/api/export", ["标注导出"]),
            RouteEntry(detection_api, "/api/detection", ["目标检测"]),
            RouteEntry(segmentation_api, "/api/segmentation", ["图像分割"]),
            RouteEntry(files_api, "/api/files", ["文件服务"]),
        ]

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


# ============== 根路由 (兼容旧 default get_routes 实现) ==============
_tasks_router = APIRouter()


# ============== 应用注册 ==============
AppRegistry.register(TasksApp())


__all__ = ["TasksApp", "tasks_app"]

# 便捷引用: app.tasks.tasks_app
tasks_app = TasksApp()
