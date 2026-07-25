"""
segmentation API Package (v3.0.0 Phase S2 拆分)
==============================================

**职责**: 图像分割任务的 mask CRUD + 训练/自动标注 + 进度推送

**目录结构** (拆分自原 segmentation.py, 564 行 → 4 子模块):
- masks.py       Mask CRUD (4 路由: upload/get/delete/replace) + _ensure_segmentation_image + _read_mask_png
- train.py       训练启动 + 自动标注启动 (2 路由)
- progress.py    进度查询 (3 路由: jobs 老 + progress 新 + SSE) + _resolve_segmentation_task_progress

**v3.0.0 Phase S2 拆分**:
- 从原 segmentation.py (564 行) 拆出 3 个子模块, 按职责切分
- 工具函数跟随业务模块 (mask 解析 → masks.py, 进度解析 → progress.py)
- 旧 segmentation.py 文件已删除 (避免与本包同名冲突)

**对外接口 (完全向后兼容)**: 9 个路由保持不变
- POST   /api/segmentation/masks/upload/{image_id}        (masks)
- GET    /api/segmentation/masks/{image_id}              (masks)
- DELETE /api/segmentation/masks/{mask_id}               (masks)
- POST   /api/segmentation/masks/replace                 (masks)
- POST   /api/segmentation/train                         (train)
- POST   /api/segmentation/auto-annotate                 (train)
- GET    /api/segmentation/jobs/{job_id}/progress        (progress, 老接口)
- GET    /api/segmentation/progress/{task_id}            (progress, v2.5.35 新增)
- GET    /api/segmentation/progress/stream/{task_id}     (progress, SSE)
"""
from fastapi import APIRouter

from app.tasks.api.segmentation.masks import router as masks_router
from app.tasks.api.segmentation.train import router as train_router
from app.tasks.api.segmentation.progress import router as progress_router

# 拼装顶层 router
router = APIRouter()
router.include_router(masks_router)
router.include_router(train_router)
router.include_router(progress_router)

# 命名导出 (供 tasks/__init__.py import)
segmentation_router = router  # 兼容旧别名


__all__ = [
    "router",
    "segmentation_router",
    "masks_router",
    "train_router",
    "progress_router",
]
