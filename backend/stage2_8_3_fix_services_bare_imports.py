"""
Stage 2.8.3 后置脚本 V4: 修复 from app.services import X 这种"无模块后缀"形式

把 `from app.services import X` 替换为:
- 如果 X 是工具/服务, 从对应的新位置 import
- 如果 X 是 classes (TrainingService/JobStateService/AutoAnnotateService/TrainingLifecycleService/TrainingDataService), 从 app.tasks.service import
- 如果 X 是 ai_service/storage_service 实例, 从 app.common.* import
"""
import re
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend\app")

# from app.services import X 形式
SERVICE_NAME_MAP = {
    # 业务服务 (从 app.tasks.service)
    "TrainingService": "app.tasks.service.training_service",
    "JobStateService": "app.tasks.service.job_state_service",
    "JobStateSnapshot": "app.tasks.service.job_state_service",
    "AutoAnnotateService": "app.tasks.service.auto_annotate_service",
    "AutoAnnotateResult": "app.tasks.service.auto_annotate_service",
    "ASYNC_THRESHOLD": "app.tasks.service.auto_annotate_service",
    "TrainingLifecycleService": "app.tasks.service.training_lifecycle_service",
    "TrainingDataService": "app.tasks.service.training_data_service",
    "ImageService": "app.tasks.service.image_service",
    "DatasetService": "app.tasks.service.dataset_service",
    "ModelService": "app.tasks.service.model_service",
    "DetectionService": "app.tasks.service.detection_service",
    "SegmentationService": "app.tasks.service.segmentation_service",
    "AnnotationService": "app.tasks.service.annotation_service",
    # 跨应用服务
    "UserService": "app.admin.service.user_service",
    "StatsService": "app.admin.service.stats_service",
    "AuthService": "app.auth.service.auth_service",
    # 工具
    "ai_service": "app.common.ml.ai_service",
    "storage_service": "app.common.storage.storage_service",
    "AIService": "app.common.ml.ai_service",
    "StorageService": "app.common.storage.storage_service",
    "BBox": "app.common.geometry.bbox_service",
    "validate_normalized_bbox": "app.common.geometry.bbox_service",
    "normalized_to_pixels": "app.common.geometry.bbox_service",
    "pixels_to_normalized": "app.common.geometry.bbox_service",
    "iou": "app.common.geometry.bbox_service",
    "nms": "app.common.geometry.bbox_service",
    "class_wise_nms": "app.common.geometry.bbox_service",
    "bbox_from_dict": "app.common.geometry.bbox_service",
    "bbox_to_yolo_line": "app.common.geometry.bbox_service",
    "yolo_line_to_bbox": "app.common.geometry.bbox_service",
    "filter_predictions_to_categories": "app.common.ml.ai_service",
}


def fix_file(path: Path) -> int:
    if "app/services" in str(path) or "app\\services" in str(path):
        return 0

    content = path.read_text(encoding="utf-8")
    new_content = content

    # 匹配 from app.services import X, Y, Z
    def replace_import(match):
        names_str = match.group(1)
        names = [n.strip() for n in names_str.split(",")]
        # 按名字分组到不同的 import 语句
        groups = {}
        for name in names:
            new_module = SERVICE_NAME_MAP.get(name)
            if new_module:
                groups.setdefault(new_module, []).append(name)
            else:
                # 未知名字, 保留原样
                groups.setdefault("app.services", []).append(name)

        if len(groups) == 1 and "app.services" in groups:
            # 全部未知, 保留
            return match.group(0)

        # 生成多行 import
        lines = []
        for module, ns in sorted(groups.items()):
            if module == "app.services":
                # 未知名字, 仍保留
                lines.append(f"from app.services import {', '.join(ns)}")
            else:
                lines.append(f"from {module} import {', '.join(ns)}")
        return "\n".join(lines)

    new_content = re.sub(
        r"from app\.services import ([^\n]+)",
        replace_import,
        new_content,
    )

    if new_content != content:
        path.write_text(new_content, encoding="utf-8")
        return 1
    return 0


def main():
    fixed = 0
    for py_file in BACKEND_ROOT.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        if fix_file(py_file):
            print(f"[FIX] {py_file.relative_to(BACKEND_ROOT.parent)}")
            fixed += 1
    print(f"\nTotal fixed: {fixed}")


if __name__ == "__main__":
    main()
