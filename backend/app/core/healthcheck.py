"""
Quick Healthcheck - 编译所有 backend/app + scripts 下的 Python 文件
====================================================================
- 全部 *.py 通过 py_compile (排除 migrations/venv)
- 统计关键指标
- 退出码非 0 即表示发现语法错误

通过 console_script `healthcheck` 调用: `uv run healthcheck`
"""
import py_compile
import sys
from pathlib import Path


def _backend_root() -> Path:
    """app/core/healthcheck.py → 上 2 层 = backend/ 根"""
    return Path(__file__).resolve().parent.parent.parent


def _iter_py_files(base: Path):
    """递归找 .py，跳过缓存目录与 venv"""
    skip = ("__pycache__", "venv", ".venv", "node_modules")
    for p in base.rglob("*.py"):
        if any(k in p.parts for k in skip):
            continue
        yield str(p)


def main() -> int:
    backend = _backend_root()
    targets = [backend / "app", backend / "scripts"]
    errors: list[tuple[str, str]] = []
    total = 0

    for root in targets:
        if not root.exists():
            continue
        for f in _iter_py_files(root):
            total += 1
            try:
                py_compile.compile(f, doraise=True)
            except py_compile.PyCompileError as e:
                msg = e.msg if e.msg else "error"
                last_line = str(msg).splitlines()[-1] if msg else "error"
                errors.append((f, last_line))

    print(f"Healthcheck: scanned {total} python files under backend/app & backend/scripts")
    if errors:
        print(f"FAILED: {len(errors)} syntax errors")
        for f, msg in errors[:20]:
            print(f"  - {f}: {msg}")
        return 1
    print("OK: all Python files compile cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
