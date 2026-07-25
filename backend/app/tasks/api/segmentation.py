"""Tasks API: segmentation routes (过渡: re-export from app.api.segmentation)

**v3.0.0 Stage 2.5 迁移**: 后续 Stage 5 将完整迁移
"""
from app.api.segmentation import router  # noqa: F401


__all__ = ["router"]
