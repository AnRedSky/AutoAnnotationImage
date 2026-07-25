"""
Stage 2.8.3 后置脚本: 修复 app.tasks.workers.X_tasks 错误路径 (X 是 detection/segmentation/tasks)
"""
import re
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend\app")

# 错误路径 → 正确路径
RENAMES = [
    (r"\bapp\.tasks\.workers\.tasks\b", "app.tasks.workers.classification"),
    (r"\bapp\.tasks\.workers\.detection_tasks\b", "app.tasks.workers.detection"),
    (r"\bapp\.tasks\.workers\.segmentation_tasks\b", "app.tasks.workers.segmentation"),
]


def fix_file(path: Path) -> int:
    content = path.read_text(encoding="utf-8")
    new_content = content
    for pattern, replacement in RENAMES:
        new_content = re.sub(pattern, replacement, new_content)
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
