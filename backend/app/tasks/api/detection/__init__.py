"""
detection API Package (v3.0.0 Phase P 拆分)
============================================

**职责**: 目标检测 (detection) 任务相关 API

**目录结构** (拆分自原 detection.py, 980 行 → 4 子模块):
- annotations.py   BBox 标注 CRUD (6 路由: save/replace/list/clear/delete/batch)
- train.py         训练启动 + 自动标注 (3 路由: train/auto-annotate/auto-annotate-pretrained)
- progress.py      进度查询 (老/新) + SSE 实时推送 (4 路由)
- models.py        模型激活/取消 + 跨图复制建议 (3 路由)

**v3.0.0 Phase P 拆分**:
- 从原 detection.py (980 行) 拆出 4 个子模块
- 工具函数下沉到所属模块 (_ensure_detection_image → annotations, _get_coco_class_names → train, _resolve_detection_task_progress → progress, _lock_dataset_models → models)
- 旧 detection.py 文件已删除 (避免与本包同名冲突)

**对外接口 (完全向后兼容)**: 16 个路由保持不变
- POST   /api/detection/annotations/save
- POST   /api/detection/annotations/replace
- GET    /api/detection/annotations/{image_id}
- DELETE /api/detection/annotations/clear/{image_id}
- DELETE /api/detection/annotations/{bbox_id}
- POST   /api/detection/annotations/batch
- POST   /api/detection/train
- POST   /api/detection/auto-annotate
- POST   /api/detection/auto-annotate-pretrained
- GET    /api/detection/jobs/{job_id}/progress
- GET    /api/detection/jobs/{job_id}/stream
- GET    /api/detection/progress/{task_id}
- GET    /api/detection/progress/stream/{task_id}
- POST   /api/detection/models/{model_id}/activate
- POST   /api/detection/models/{model_id}/deactivate
- GET    /api/detection/copy-suggestion/{image_id}
"""
from fastapi import APIRouter

from app.tasks.api.detection.annotations import router as annotations_router
from app.tasks.api.detection.train import router as train_router
from app.tasks.api.detection.progress import router as progress_router
from app.tasks.api.detection.models import router as models_router

# 拼装顶层 router
router = APIRouter()
router.include_router(annotations_router)
router.include_router(train_router)
router.include_router(progress_router)
router.include_router(models_router)

# 命名导出 (供 tasks/__init__.py import)
detection_router = router  # 兼容旧别名


__all__ = [
    "router",
    "detection_router",
    "annotations_router",
    "train_router",
    "progress_router",
    "models_router",
]
