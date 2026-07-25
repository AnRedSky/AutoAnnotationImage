"""
Annotation Application — 业务应用 4/4
=====================================

**职责**:
- 图像分类标注 (人工确认 AI 标签 / 人工修正)
- 目标检测 BBox 标注 (CRUD)
- 图像分割 Mask 标注 (CRUD)
- 标注历史查询 (AnnotationLog)
- 统一标注动作入口 (派发 3 种任务类型)

**应用特征**:
- 名称: annotation
- 版本: 3.0.0
- API 前缀: /api/annotations
- 依赖: app.common, app.database, app.middleware, app.utils, app.tasks.model.{Image, Category, BBoxAnnotation, SegmentationMask}

**目录约定**:
- api/        路由层 (annotation.py)
- service/    业务编排 (AnnotationService, BBoxService)
- schema/     Pydantic DTO (AnnotationAction, BBoxIn, MaskIn)
- repository/ 复杂查询 (annotation_repo, bbox_repo, mask_repo)

**重要约束**:
- 不直接 import app.tasks.service, 而是通过 app.tasks.model 引用模型
- 标注写入后通过 EventBus 发布 annotation.confirmed/corrected, 由 tasks/ML 监听
- 不持有 Image/Category 的写逻辑, 只持有标注本身的写逻辑

**v3.0.0 Stage 2 新增**: 多应用架构骨架
"""
from fastapi import APIRouter

from app.common.interfaces import AppInterface
from app.registry import AppRegistry


class AnnotationApp(AppInterface):
    """Annotation 应用 — 标注管理 (分类/检测/分割统一入口)"""

    @property
    def name(self) -> str:
        return "annotation"

    @property
    def version(self) -> str:
        return "3.0.0"

    @property
    def router(self) -> APIRouter:
        return _annotation_router

    def register_events(self) -> list[str]:
        # Annotation 是被订阅方 (上游), 主动发布事件
        return []


# ============== 根路由 (Stage 2.5 之后会聚合 annotation 路由) ==============
_annotation_router = APIRouter()

# Stage 2.5 完成后, 这里会类似:
# from app.annotation.api.annotation import router as annotation_router
# _annotation_router.include_router(annotation_router, tags=["标注管理"])


# ============== 应用注册 ==============
AppRegistry.register(AnnotationApp())


__all__ = ["AnnotationApp", "annotation_app"]

# 便捷引用: app.annotation.annotation_app
annotation_app = AnnotationApp()
