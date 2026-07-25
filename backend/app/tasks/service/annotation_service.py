"""
AnnotationService — Backward-Compatibility Shim (Lazy Import)
============================================================

**v3.0.0 审查修复**: AnnotationService 已从本模块 (`app.tasks.service.annotation_service`)
迁入 annotation 应用 (`app.annotation.service.annotation_service`).

历史背景: 该类最初是 Phase 3 时为统一三类任务标注入口而创建, 因早期 annotation
应用尚未独立, 暂放在 tasks 应用. 实际语义是"标注业务统一入口", 归属 annotation
应用更合理.

**本模块职责**: 仅作为向后兼容垫片, 用 `__getattr__` 延迟加载, 避免循环导入.
annotation.service.annotation_service 会反向 import tasks.service.image_service,
而 tasks.service.__init__ 历史上会 import 本模块 → 循环. lazy 加载可解.

**未来**: 建议新代码直接 `from app.annotation.service import AnnotationService`.
"""
from typing import Any

__all__ = ["AnnotationService"]


def __getattr__(name: str) -> Any:
    """PEP 562 lazy module attribute access."""
    if name == "AnnotationService":
        from app.annotation.service.annotation_service import AnnotationService
        return AnnotationService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
