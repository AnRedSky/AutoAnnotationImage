"""
Stage 2.8.2 验证脚本: 检查所有 app/{admin,auth,tasks,annotation}/api/*.py 内部 import 是否正确
"""
import ast
import sys
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend")

# 检查不应该出现的旧路径
FORBIDDEN_PATTERNS = [
    "app.api.",
    "app.model.",
    "app.workers.",
    "app.ml.",
    "from app.config",  # 应改为 app.core.config
    "app.core.deps",
]


def check_file(path: Path) -> list:
    """检查单个文件"""
    issues = []
    content = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError as e:
        issues.append(f"SYNTAX ERROR: {e}")
        return issues

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
    targets = [
        BACKEND_ROOT / "app" / "admin" / "api",
        BACKEND_ROOT / "app" / "auth" / "api",
        BACKEND_ROOT / "app" / "tasks" / "api",
        BACKEND_ROOT / "app" / "annotation" / "api",
    ]

    total_issues = 0
    for target_dir in targets:
        if not target_dir.exists():
            print(f"[WARN] {target_dir} not found")
            continue
        for py_file in sorted(target_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            issues = check_file(py_file)
            if issues:
                print(f"\n[ISSUES] {py_file.relative_to(BACKEND_ROOT)}:")
                for issue in issues:
                    print(issue)
                total_issues += len(issues)
            else:
                print(f"[OK] {py_file.relative_to(BACKEND_ROOT)}")

    print(f"\n=== Total issues: {total_issues} ===")
    return 0 if total_issues == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
