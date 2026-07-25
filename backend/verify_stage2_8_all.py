"""
Stage 2.8 综合验证脚本: 检查所有 app/api/, app/services/, app/model/, app/ml/, app/workers/ 引用

排除:
- app/api/ 目录 (待删除)
- app/services/ 目录 (待删除)
- app/model/ 目录 (待删除)
- app/ml/ 目录 (待删除)
- app/workers/ 目录 (待删除)
- app/common/base_model.py (Base 来自 common)
- app/core/deps.py (兼容垫片保留)
- app/config.py (兼容垫片保留)
"""
import ast
import sys
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend\app")

# 排除目录 (这些目录中的文件是兼容垫片或要被删除)
EXCLUDE_DIRS = {
    "app/api",
    "app/services",
    "app/model",
    "app/ml",
    "app/workers",
    "app/database",  # 新位置, 有 from app.model.base
    "app/common/base_model",  # 特殊
    "app/core/deps",  # 兼容垫片
    "app/config",  # 兼容垫片
    "app/__pycache__",
}

# 检查不应该出现的旧路径
FORBIDDEN_PATTERNS = [
    "app.api.",
    "app.model.",
    "app.workers.",
    "app.ml.",
    "from app.config",
    "app.core.deps",
    "app.services.",  # 所有 app.services 引用
]


def is_excluded(path: Path) -> bool:
    """检查文件是否在排除目录中"""
    rel = path.relative_to(BACKEND_ROOT.parent).as_posix()
    for exclude in EXCLUDE_DIRS:
        if rel.startswith(exclude.replace("\\", "/")):
            return True
    return False


def check_file(path: Path) -> list:
    """检查单个文件"""
    if is_excluded(path):
        return []

    issues = []
    content = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError as e:
        return [f"SYNTAX ERROR: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for pattern in FORBIDDEN_PATTERNS:
                if pattern in module:
                    issues.append(
                        f"  L{node.lineno}: from {module} import ... (forbidden: {pattern})"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for pattern in FORBIDDEN_PATTERNS:
                    if pattern in alias.name:
                        issues.append(
                            f"  L{node.lineno}: import {alias.name} (forbidden: {pattern})"
                        )
    return issues


def main():
    total_issues = 0
    for py_file in sorted(BACKEND_ROOT.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        if is_excluded(py_file):
            continue
        issues = check_file(py_file)
        if issues:
            print(f"\n[ISSUES] {py_file.relative_to(BACKEND_ROOT.parent)}:")
            for issue in issues:
                print(issue)
            total_issues += len(issues)

    print(f"\n=== Total issues: {total_issues} ===")
    return 0 if total_issues == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
