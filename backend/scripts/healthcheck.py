"""
Stage 1~5 + Phase I 快速健康检查脚本
- 全部 *.py 通过 py_compile (排除 migrations/venv)
- 统计关键指标
"""
import py_compile
import pathlib
import sys

ROOT = pathlib.Path("d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app")
SKIP = ("migrations", "venv", "__pycache__")

errors: list[tuple[str, str]] = []
total = 0
for p in ROOT.rglob("*.py"):
    s = str(p)
    if any(k in s for k in SKIP):
        continue
    total += 1
    try:
        py_compile.compile(s, doraise=True)
    except py_compile.PyCompileError as e:
        errors.append((s, str(e).splitlines()[-1] if e.msg else "error"))

print(f"Total py files checked: {total}")
if errors:
    print(f"FAILED: {len(errors)} errors")
    for f, msg in errors[:10]:
        print(f"  - {f}: {msg}")
    sys.exit(1)
print("OK: all Python files compile cleanly")
