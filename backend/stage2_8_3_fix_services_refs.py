"""
Stage 2.8.3 后置脚本 V2: 全局修复 app.services.* 引用

跳过:
- app/services/ 目录 (这些是要删除的)
- 文件中 docstring/注释里的 app.services.* 字符串 (允许保留)
"""
import re
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend\app")

# app.services.X 映射表 (按长到短顺序, 避免短匹配覆盖长)
SERVICE_RENAMES = [
    ("app.services.training_lifecycle_service", "app.tasks.service.training_lifecycle_service"),
    ("app.services.training_data_service", "app.tasks.service.training_data_service"),
    ("app.services.training_service", "app.tasks.service.training_service"),
    ("app.services.job_state_service", "app.tasks.service.job_state_service"),
    ("app.services.auto_annotate_service", "app.tasks.service.auto_annotate_service"),
    ("app.services.annotation_service", "app.tasks.service.annotation_service"),
    ("app.services.detection_service", "app.tasks.service.detection_service"),
    ("app.services.segmentation_service", "app.tasks.service.segmentation_service"),
    ("app.services.image_service", "app.tasks.service.image_service"),
    ("app.services.dataset_service", "app.tasks.service.dataset_service"),
    ("app.services.model_service", "app.tasks.service.model_service"),
    ("app.services.user_service", "app.admin.service.user_service"),
    ("app.services.stats_service", "app.admin.service.stats_service"),
    ("app.services.auth_service", "app.auth.service.auth_service"),
    # 工具服务
    ("app.services.storage_service", "app.common.storage.storage_service"),
    ("app.services.bbox_service", "app.common.geometry.bbox_service"),
    ("app.services.ai_service", "app.common.ml.ai_service"),
]


def fix_file(path: Path) -> int:
    """修复单个文件"""
    if "app/services" in str(path):  # 跳过 services 目录
        return 0
    if "app\\services" in str(path):
        return 0

    content = path.read_text(encoding="utf-8")
    new_content = content
    for old, new in SERVICE_RENAMES:
        new_content = new_content.replace(old, new)
    if new_content != content:
        path.write_text(new_content, encoding="utf-8")
        return 1
    return 0


def main():
    fixed = 0
    for py_file in BACKEND_ROOT.rglob("*.py"):
        if fix_file(py_file):
            print(f"[FIX] {py_file.relative_to(BACKEND_ROOT)}")
            fixed += 1
    print(f"\nTotal fixed: {fixed}")


if __name__ == "__main__":
    main()
