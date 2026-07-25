"""
Stage 2.8.3 脚本: 把 app/services/*.py 业务服务迁移到 app/tasks/service/,
工具服务迁移到 app/common/.

执行:
- 业务服务 (annotation/auto_annotate/detection/job_state/segmentation/training_data/training_lifecycle/training)
  → app/tasks/service/
- 工具服务:
  - ai_service → app/common/ml/ai_service.py
  - bbox_service → app/common/geometry/bbox_service.py
  - storage_service → app/common/storage/storage_service.py
- 删除已迁的兼容垫片 (auth/dataset/image/model/stats/user_service.py)
- 重写 app/services/__init__.py
"""
import re
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend")
APP_SERVICES = BACKEND_ROOT / "app" / "services"


def rewrite_imports(content: str) -> str:
    """重写 import + 字符串字面量中的旧路径"""
    replacements = [
        # model 按模块名精确替换
        (r"\bapp\.model\.user\b", "app.admin.model.user"),
        (r"\bapp\.model\.dataset\b", "app.tasks.model.dataset"),
        (r"\bapp\.model\.category\b", "app.tasks.model.category"),
        (r"\bapp\.model\.image\b", "app.tasks.model.image"),
        (r"\bapp\.model\.annotation_log\b", "app.tasks.model.annotation_log"),
        (r"\bapp\.model\.model_version\b", "app.tasks.model.model_version"),
        (r"\bapp\.model\.training_job\b", "app.tasks.model.training_job"),
        (r"\bapp\.model\.bbox_annotation\b", "app.annotation.model.bbox_annotation"),
        (r"\bapp\.model\.segmentation_mask\b", "app.annotation.model.segmentation_mask"),
        # 通用 model.X 兜底
        (r"\bapp\.model\.([a-z_]+)\b", r"app.tasks.model.\1"),
        # config → core.config
        (r"(?<![\w.])app\.config(?!\.\w)", "app.core.config"),
        # core.deps → middleware.http.auth
        (r"\bapp\.core\.deps\b", "app.middleware.http.auth"),
        # workers → tasks.workers
        (r"\bapp\.workers\b", "app.tasks.workers"),
        # ml → tasks.ml
        (r"\bapp\.ml\b", "app.tasks.ml"),
        # 内部 service 互引 (服务互调) — 处理 app.services.X 引用其他服务
        (r"\bapp\.services\.annotation_service\b", "app.tasks.service.annotation_service"),
        (r"\bapp\.services\.auto_annotate_service\b", "app.tasks.service.auto_annotate_service"),
        (r"\bapp\.services\.detection_service\b", "app.tasks.service.detection_service"),
        (r"\bapp\.services\.job_state_service\b", "app.tasks.service.job_state_service"),
        (r"\bapp\.services\.segmentation_service\b", "app.tasks.service.segmentation_service"),
        (r"\bapp\.services\.training_service\b", "app.tasks.service.training_service"),
        (r"\bapp\.services\.training_lifecycle_service\b", "app.tasks.service.training_lifecycle_service"),
        (r"\bapp\.services\.training_data_service\b", "app.tasks.service.training_data_service"),
        (r"\bapp\.services\.image_service\b", "app.tasks.service.image_service"),
        (r"\bapp\.services\.dataset_service\b", "app.tasks.service.dataset_service"),
        (r"\bapp\.services\.model_service\b", "app.tasks.service.model_service"),
        # 工具服务
        (r"\bapp\.services\.ai_service\b", "app.common.ml.ai_service"),
        (r"\bapp\.services\.bbox_service\b", "app.common.geometry.bbox_service"),
        (r"\bapp\.services\.storage_service\b", "app.common.storage.storage_service"),
        (r"\bapp\.services\.auth_service\b", "app.auth.service.auth_service"),
        (r"\bapp\.services\.user_service\b", "app.admin.service.user_service"),
        (r"\bapp\.services\.stats_service\b", "app.admin.service.stats_service"),
    ]

    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content)

    # 处理 Celery task 名字符串 (在 _TASK_DISPATCH 等 dict 中)
    content = content.replace(
        "app.workers.tasks:train_model_task",
        "app.tasks.workers.classification:train_model_task"
    )
    content = content.replace(
        "app.workers.detection_tasks:train_detection_task",
        "app.tasks.workers.detection:train_detection_task"
    )
    content = content.replace(
        "app.workers.segmentation_tasks:train_segmentation_task",
        "app.tasks.workers.segmentation:train_segmentation_task"
    )
    content = content.replace(
        "app.workers.tasks:auto_annotate_task",
        "app.tasks.workers.classification:auto_annotate_task"
    )
    content = content.replace(
        "app.workers.detection_tasks:auto_annotate_detection_task",
        "app.tasks.workers.detection:auto_annotate_detection_task"
    )
    content = content.replace(
        "app.workers.segmentation_tasks:auto_annotate_segmentation_task",
        "app.tasks.workers.segmentation:auto_annotate_segmentation_task"
    )

    return content


# 业务服务迁移目标
BUSINESS_TARGETS = {
    "annotation_service.py": "app/tasks/service/annotation_service.py",
    "auto_annotate_service.py": "app/tasks/service/auto_annotate_service.py",
    "detection_service.py": "app/tasks/service/detection_service.py",
    "job_state_service.py": "app/tasks/service/job_state_service.py",
    "segmentation_service.py": "app/tasks/service/segmentation_service.py",
    "training_data_service.py": "app/tasks/service/training_data_service.py",
    "training_lifecycle_service.py": "app/tasks/service/training_lifecycle_service.py",
    "training_service.py": "app/tasks/service/training_service.py",
}

# 工具服务迁移目标
UTILITY_TARGETS = {
    "ai_service.py": "app/common/ml/ai_service.py",
    "bbox_service.py": "app/common/geometry/bbox_service.py",
    "storage_service.py": "app/common/storage/storage_service.py",
}


def main():
    # Step 1: 迁移业务服务
    for src_name, target_rel in BUSINESS_TARGETS.items():
        src_path = APP_SERVICES / src_name
        target_path = BACKEND_ROOT / target_rel

        if not src_path.exists():
            print(f"[WARN] source not found: {src_path}")
            continue

        print(f"\n[BUSINESS] {src_name}")
        print(f"  src:    {src_path}")
        print(f"  target: {target_path}")

        content = src_path.read_text(encoding="utf-8")
        new_content = rewrite_imports(content)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_content, encoding="utf-8")
        print(f"  [OK] wrote {target_path} ({len(new_content)} bytes)")

    # Step 2: 迁移工具服务
    for src_name, target_rel in UTILITY_TARGETS.items():
        src_path = APP_SERVICES / src_name
        target_path = BACKEND_ROOT / target_rel

        if not src_path.exists():
            print(f"[WARN] source not found: {src_path}")
            continue

        print(f"\n[UTILITY] {src_name}")
        print(f"  src:    {src_path}")
        print(f"  target: {target_path}")

        content = src_path.read_text(encoding="utf-8")
        new_content = rewrite_imports(content)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_content, encoding="utf-8")
        print(f"  [OK] wrote {target_path} ({len(new_content)} bytes)")


if __name__ == "__main__":
    main()
