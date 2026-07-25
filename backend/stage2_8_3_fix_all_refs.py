"""
Stage 2.8.3 后置脚本 V3: 全 app 范围修复 model/workers/ml/services 引用

跳过:
- app/api/ (待删除)
- app/services/ (待删除)
- app/model/ (待删除)
- app/ml/ (待删除)
- app/workers/ (待删除)
- app/database/ (兼容垫片)
- app/common/base_model.py
- app/core/deps.py
- app/config.py
"""
import re
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend\app")

EXCLUDE_DIRS = {
    "app/api",
    "app/services",
    "app/model",
    "app/ml",
    "app/workers",
    "app/database",
    "app/common/base_model",
    "app/core/deps",
    "app/config",
}

# 全部旧路径替换规则
RENAMES = [
    # ---- model 重映射 ----
    (r"\bapp\.model\.user\b", "app.admin.model.user"),
    (r"\bapp\.model\.dataset\b", "app.tasks.model.dataset"),
    (r"\bapp\.model\.category\b", "app.tasks.model.category"),
    (r"\bapp\.model\.image\b", "app.tasks.model.image"),
    (r"\bapp\.model\.annotation_log\b", "app.tasks.model.annotation_log"),
    (r"\bapp\.model\.model_version\b", "app.tasks.model.model_version"),
    (r"\bapp\.model\.training_job\b", "app.tasks.model.training_job"),
    (r"\bapp\.model\.bbox_annotation\b", "app.annotation.model.bbox_annotation"),
    (r"\bapp\.model\.segmentation_mask\b", "app.annotation.model.segmentation_mask"),
    (r"\bapp\.model\.([a-z_]+)\b", r"app.tasks.model.\1"),
    # ---- config ----
    (r"(?<![\w.])app\.config(?!\.\w)", "app.core.config"),
    (r"\bapp\.core\.deps\b", "app.middleware.http.auth"),
    # ---- workers ----
    (r"\bapp\.workers\.tasks\b", "app.tasks.workers.classification"),
    (r"\bapp\.workers\.detection_tasks\b", "app.tasks.workers.detection"),
    (r"\bapp\.workers\.segmentation_tasks\b", "app.tasks.workers.segmentation"),
    (r"\bapp\.workers\.classification\b", "app.tasks.workers.classification"),
    (r"\bapp\.workers\.detection\b", "app.tasks.workers.detection"),
    (r"\bapp\.workers\.segmentation\b", "app.tasks.workers.segmentation"),
    (r"\bapp\.workers\.celery_app\b", "app.tasks.workers.celery_app"),
    (r"\bapp\.workers\b", "app.tasks.workers"),  # 兜底
    # ---- ml ----
    (r"\bapp\.ml\.train\b", "app.tasks.ml.classification"),
    (r"\bapp\.ml\.classification\b", "app.tasks.ml.classification"),
    (r"\bapp\.ml\.detection\b", "app.tasks.ml.detection"),
    (r"\bapp\.ml\.segmentation\b", "app.tasks.ml.segmentation"),
    (r"\bapp\.ml\b", "app.tasks.ml"),  # 兜底
    # ---- services ----
    (r"\bapp\.services\.training_lifecycle_service\b", "app.tasks.service.training_lifecycle_service"),
    (r"\bapp\.services\.training_data_service\b", "app.tasks.service.training_data_service"),
    (r"\bapp\.services\.training_service\b", "app.tasks.service.training_service"),
    (r"\bapp\.services\.job_state_service\b", "app.tasks.service.job_state_service"),
    (r"\bapp\.services\.auto_annotate_service\b", "app.tasks.service.auto_annotate_service"),
    (r"\bapp\.services\.annotation_service\b", "app.tasks.service.annotation_service"),
    (r"\bapp\.services\.detection_service\b", "app.tasks.service.detection_service"),
    (r"\bapp\.services\.segmentation_service\b", "app.tasks.service.segmentation_service"),
    (r"\bapp\.services\.image_service\b", "app.tasks.service.image_service"),
    (r"\bapp\.services\.dataset_service\b", "app.tasks.service.dataset_service"),
    (r"\bapp\.services\.model_service\b", "app.tasks.service.model_service"),
    (r"\bapp\.services\.user_service\b", "app.admin.service.user_service"),
    (r"\bapp\.services\.stats_service\b", "app.admin.service.stats_service"),
    (r"\bapp\.services\.auth_service\b", "app.auth.service.auth_service"),
    (r"\bapp\.services\.storage_service\b", "app.common.storage.storage_service"),
    (r"\bapp\.services\.bbox_service\b", "app.common.geometry.bbox_service"),
    (r"\bapp\.services\.ai_service\b", "app.common.ml.ai_service"),
]

# Celery task 名字符串
TASK_NAME_RENAMES = [
    ("app.tasks.workers.tasks:train_model_task", "app.tasks.workers.classification:train_model_task"),
    ("app.tasks.workers.detection_tasks:train_detection_task", "app.tasks.workers.detection:train_detection_task"),
    ("app.tasks.workers.segmentation_tasks:train_segmentation_task", "app.tasks.workers.segmentation:train_segmentation_task"),
    ("app.tasks.workers.tasks:auto_annotate_task", "app.tasks.workers.classification:auto_annotate_task"),
    ("app.tasks.workers.detection_tasks:auto_annotate_detection_task", "app.tasks.workers.detection:auto_annotate_detection_task"),
    ("app.tasks.workers.segmentation_tasks:auto_annotate_segmentation_task", "app.tasks.workers.segmentation:auto_annotate_segmentation_task"),
    ("app.workers.tasks:train_model_task", "app.tasks.workers.classification:train_model_task"),
    ("app.workers.detection_tasks:train_detection_task", "app.tasks.workers.detection:train_detection_task"),
    ("app.workers.segmentation_tasks:train_segmentation_task", "app.tasks.workers.segmentation:train_segmentation_task"),
    ("app.workers.tasks:auto_annotate_task", "app.tasks.workers.classification:auto_annotate_task"),
    ("app.workers.detection_tasks:auto_annotate_detection_task", "app.tasks.workers.detection:auto_annotate_detection_task"),
    ("app.workers.segmentation_tasks:auto_annotate_segmentation_task", "app.tasks.workers.segmentation:auto_annotate_segmentation_task"),
]


def is_excluded(path: Path) -> bool:
    rel = path.relative_to(BACKEND_ROOT.parent).as_posix()
    for exclude in EXCLUDE_DIRS:
        if rel.startswith(exclude.replace("\\", "/")):
            return True
    return False


def fix_file(path: Path) -> int:
    if is_excluded(path):
        return 0

    content = path.read_text(encoding="utf-8")
    new_content = content

    for pattern, replacement in RENAMES:
        new_content = re.sub(pattern, replacement, new_content)

    for old, new in TASK_NAME_RENAMES:
        new_content = new_content.replace(old, new)

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
