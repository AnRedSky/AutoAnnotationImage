"""
export API Package (v3.0.0 Phase S1 拆分)
=========================================

**职责**: 标注导出 (3 种任务类型 × 2-3 种格式)

**目录结构** (拆分自原 export.py, 720 行 → 4 子模块):
- classification.py  分类导出 (3 路由: coco/yolo/csv) + _filter_images_by_status 工具
- detection.py       检测导出 (2 路由: coco-det/yolo-det)
- segmentation.py    分割导出 (2 路由: voc-seg/coco-seg) + 2 路径解析工具

**v3.0.0 Phase S1 拆分**:
- 从原 export.py (720 行) 拆出 3 个子模块, 按任务类型 (classification/detection/segmentation) 切分
- 共享的状态过滤逻辑 `_filter_images_by_status` 抽离到 classification.py (分类最常用, detection 复用)
- 工具函数 `_resolve_mask_path` / `_resolve_image_path_for_export` 跟随 segmentation.py (只有它用)
- 旧 export.py 文件已删除 (避免与本包同名冲突)

**对外接口 (完全向后兼容)**: 7 个路由保持不变
- GET /api/export/coco/{dataset_id}          (classification)
- GET /api/export/yolo/{dataset_id}          (classification)
- GET /api/export/csv/{dataset_id}           (classification)
- GET /api/export/yolo-det/{dataset_id}      (detection)
- GET /api/export/coco-det/{dataset_id}      (detection)
- GET /api/export/voc-seg/{dataset_id}       (segmentation)
- GET /api/export/coco-seg/{dataset_id}      (segmentation)
"""
from fastapi import APIRouter

from app.tasks.api.export.classification import router as classification_router
from app.tasks.api.export.detection import router as detection_router
from app.tasks.api.export.segmentation import router as segmentation_router

# 拼装顶层 router
router = APIRouter()
router.include_router(classification_router)
router.include_router(detection_router)
router.include_router(segmentation_router)

# 命名导出 (供 tasks/__init__.py import)
export_router = router  # 兼容旧别名


__all__ = [
    "router",
    "export_router",
    "classification_router",
    "detection_router",
    "segmentation_router",
]
