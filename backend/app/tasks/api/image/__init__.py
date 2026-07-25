"""
image API Package (v3.0.0 Phase N 拆分)
========================================

**职责**: 图片上传/自动标注/置信度预览/查询/删除

**目录结构**:
- upload.py       批量上传 + 多重校验 (扩展名/MIME/magic bytes/大小)
- auto_label.py   批量 AI 预标注 (写库 + 审计)
- preview.py      非破坏性置信度测评 (三路分派 classification/detection/segmentation)
- query.py        列表查询 + 单图详情
- delete.py       单图/批量删除 (eager load + cascade)

**v3.0.0 Phase N 拆分**:
- 从原 image.py (1152 行) 拆出 5 个子模块, 每个模块只关注单一职责
- __init__.py 负责拼装: 统一前缀 + 包含各子 router
- 旧 image.py 文件已删除 (避免与本包同名冲突)

**对外接口 (保持不变, 完全向后兼容)**:
- POST /api/images/upload/{dataset_id}        (upload.py)
- POST /api/images/auto-label/{dataset_id}    (auto_label.py)
- POST /api/images/preview-confidence         (preview.py)
- GET  /api/images/list/{dataset_id}          (query.py)
- GET  /api/images/{image_id}                 (query.py)
- DELETE /api/images/{image_id}               (delete.py)
- POST /api/images/batch-delete               (delete.py)
"""
from fastapi import APIRouter

from app.tasks.api.image.upload import router as upload_router
from app.tasks.api.image.auto_label import router as auto_label_router
from app.tasks.api.image.preview import router as preview_router
from app.tasks.api.image.query import router as query_router
from app.tasks.api.image.delete import router as delete_router

# 拼装顶层 router (旧 image.py 是单 router, 拆包后通过 include_router 统一前缀)
router = APIRouter()
router.include_router(upload_router)
router.include_router(auto_label_router)
router.include_router(preview_router)
router.include_router(query_router)
router.include_router(delete_router)

# 命名导出 (供 tasks/__init__.py import)
image_router = router  # 兼容旧别名


__all__ = [
    "router",
    "image_router",
    "upload_router",
    "auto_label_router",
    "preview_router",
    "query_router",
    "delete_router",
]
