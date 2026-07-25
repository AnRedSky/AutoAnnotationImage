"""
Annotation API Package (Stage 2.5 填充)
======================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/* 迁入 annotation 应用
"""
# Stage 2.5-2.8 完整迁移
from app.annotation.api.annotation import router as annotation_router

# 命名导出
annotation = annotation_router  # type: ignore


__all__ = [
    "annotation",
    "annotation_router",
]
