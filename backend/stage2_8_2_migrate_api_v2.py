"""
Stage 2.8.2 脚本 V2: 把 app/api/*.py 全部迁移到 app/{admin,tasks,annotation}/api/*.py
更精确的 import 重写.
"""
import re
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend")
APP_API = BACKEND_ROOT / "app" / "api"


def rewrite_imports(content: str) -> str:
    """重写 import 语句中的旧路径为新路径 - 更精确版本"""
    # 按从长到短排序, 避免短匹配覆盖长匹配
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
        # 通用 model.X 兜底 (不能精确匹配的, 默认走 tasks.model)
        (r"\bapp\.model\.([a-z_]+)\b", r"app.tasks.model.\1"),
        # config → core.config (避免误伤 app.core.config)
        (r"(?<![\w.])app\.config(?!\.\w)", "app.core.config"),
        # core.deps → middleware.http.auth
        (r"\bapp\.core\.deps\b", "app.middleware.http.auth"),
        # workers → tasks.workers
        (r"\bapp\.workers\b", "app.tasks.workers"),
        # ml → tasks.ml
        (r"\bapp\.ml\b", "app.tasks.ml"),
    ]

    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content)

    return content


# 路由迁移目标映射
TARGET_MAP = {
    "stats": ("app.admin.api", "stats"),
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


def main():
    for name, (target_pkg, target_name) in TARGET_MAP.items():
        src_path = APP_API / f"{name}.py"
        target_path = BACKEND_ROOT / target_pkg.replace(".", "/") / f"{target_name}.py"

        if not src_path.exists():
            print(f"[WARN] source not found: {src_path}")
            continue

        print(f"\n[PROCESS] {name}.py")
        print(f"  src:    {src_path}")
        print(f"  target: {target_path}")

        content = src_path.read_text(encoding="utf-8")
        new_content = rewrite_imports(content)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_content, encoding="utf-8")
        print(f"  [OK] wrote {target_path} ({len(new_content)} bytes)")


if __name__ == "__main__":
    main()
