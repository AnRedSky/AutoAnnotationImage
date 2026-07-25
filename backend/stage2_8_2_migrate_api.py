"""
Stage 2.8.2 脚本: 把 app/api/*.py 全部迁移到 app/{admin,tasks,annotation}/api/*.py

执行逻辑:
- 对于每个 app/api/<name>.py:
  1. 找到对应的目标 app/{admin,tasks,annotation}/api/<name>.py
  2. 用 app/api/<name>.py 的内容覆盖目标文件
  3. 同时把目标文件内部的 import 改写为新路径

import 改写规则 (旧 → 新):
- app.model.user.User           → app.admin.model.user.User
- app.model.dataset.Dataset     → app.tasks.model.dataset.Dataset
- app.model.category.Category   → app.tasks.model.category.Category
- app.model.image.Image         → app.tasks.model.image.Image
- app.model.annotation_log.AnnotationLog → app.tasks.model.annotation_log.AnnotationLog
- app.model.model_version.ModelVersion → app.tasks.model.model_version.ModelVersion
- app.model.training_job.TrainingJob → app.tasks.model.training_job.TrainingJob
- app.model.bbox_annotation.BBoxAnnotation → app.annotation.model.bbox_annotation.BBoxAnnotation
- app.model.segmentation_mask.SegmentationMask → app.annotation.model.segmentation_mask.SegmentationMask
- app.config.settings           → app.core.config.settings
- app.core.deps                 → app.middleware.http.auth
- app.ml.X                      → app.tasks.ml.X
- app.workers.X                 → app.tasks.workers.X
"""
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend")
APP_API = BACKEND_ROOT / "app" / "api"

# ============== 映射表 ==============
# (old, new) - 按从长到短排序, 避免短匹配覆盖长匹配
MODEL_RENAMES = [
    # (old_module_prefix, new_module_prefix)
    ("app.model.user", "app.admin.model.user"),
    ("app.model.dataset", "app.tasks.model.dataset"),
    ("app.model.category", "app.tasks.model.category"),
    ("app.model.image", "app.tasks.model.image"),
    ("app.model.annotation_log", "app.tasks.model.annotation_log"),
    ("app.model.model_version", "app.tasks.model.model_version"),
    ("app.model.training_job", "app.tasks.model.training_job"),
    ("app.model.bbox_annotation", "app.annotation.model.bbox_annotation"),
    ("app.model.segmentation_mask", "app.annotation.model.segmentation_mask"),
    # 通用 app.model.X (作为兜底)
    ("app.model", "app.tasks.model"),
]

# 路由迁移目标映射
TARGET_MAP = {
    "auth": ("app.auth.api", "auth"),
    "user": ("app.admin.api", "user"),
    "stats": ("app.admin.api", "stats"),
    "system": ("app.admin.api", "system"),
    "annotation": ("app.annotation.api", "annotation"),
    "dataset": ("app.tasks.api", "dataset"),
    "image": ("app.tasks.api", "image"),
    "training": ("app.tasks.api", "training"),
    "model": ("app.tasks.api", "model"),
    "auto_annotate": ("app.tasks.api", "auto_annotate"),
    "export": ("app.tasks.api", "export"),
    "detection": ("app.tasks.api", "detection"),
    "segmentation": ("app.tasks.api", "segmentation"),
    "files": ("app.tasks.api", "files"),
}


def rewrite_imports(content: str) -> str:
    """重写 import 语句中的旧路径为新路径"""
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        new_line = line
        # 处理 from app.model.X import Y
        for old, new in MODEL_RENAMES:
            if old in new_line:
                new_line = new_line.replace(old, new)
        # app.config → app.core.config (避免被 app.core 误伤)
        new_line = re.sub(r"\bapp\.config\b(?!\.\w)", "app.core.config", new_line)
        # app.core.deps → app.middleware.http.auth
        new_line = new_line.replace("app.core.deps", "app.middleware.http.auth")
        # app.ml.X → app.tasks.ml.X
        new_line = re.sub(r"\bapp\.ml\b(?!\.\w)", "app.tasks.ml", new_line)
        # app.workers.X → app.tasks.workers.X
        new_line = re.sub(r"\bapp\.workers\b(?!\.\w)", "app.tasks.workers", new_line)
        new_lines.append(new_line)
    return "\n".join(new_lines)


def rewrite_app_path_in_doc(content: str) -> str:
    """重写文档字符串中的路径 (app/api → app/{admin,tasks,annotation}/api)"""
    # 把模块头部 docstring 里的 "app/api/<name>.py" 改成对应的新位置
    return content


def main():
    # 已经完整迁移的（不需要做）: auth, user, system
    skip = {"auth", "user", "system", "__init__"}

    for name in sorted(TARGET_MAP.keys()):
        if name in skip:
            print(f"[SKIP] {name}.py (already fully migrated)")
            continue

        src_path = APP_API / f"{name}.py"
        target_pkg, target_name = TARGET_MAP[name]
        target_path = BACKEND_ROOT / target_pkg.replace(".", "/") / f"{target_name}.py"

        if not src_path.exists():
            print(f"[WARN] source not found: {src_path}")
            continue

        print(f"\n[PROCESS] {name}.py")
        print(f"  src:    {src_path}")
        print(f"  target: {target_path}")

        content = src_path.read_text(encoding="utf-8")
        new_content = rewrite_imports(content)

        # 写入目标
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_content, encoding="utf-8")
        print(f"  [OK] wrote {target_path} ({len(new_content)} bytes)")


if __name__ == "__main__":
    main()
