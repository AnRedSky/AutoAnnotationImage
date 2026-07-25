"""
健康统计脚本 - 输出后端 + 前端关键指标
"""
import pathlib
import json

BACKEND = pathlib.Path("d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app")
FRONTEND = pathlib.Path("d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/frontend/src")

# 排除目录
SKIP = ("migrations", "venv", "__pycache__", "node_modules", "dist", ".git")

def count_py(loc):
    return sum(1 for p in loc.rglob("*.py") if not any(k in str(p) for k in SKIP))

def count_vue(loc):
    return sum(1 for p in loc.rglob("*.vue"))

def count_ts(loc):
    return sum(1 for p in loc.rglob("*.ts") if not any(k in str(p) for k in SKIP))

def largest_files(loc, ext, n=10):
    files = []
    for p in loc.rglob(f"*.{ext}"):
        if any(k in str(p) for k in SKIP):
            continue
        files.append((str(p.relative_to(loc.parent.parent.parent)), sum(1 for _ in p.open('rb'))))
    files.sort(key=lambda x: -x[1])
    return files[:n]

stats = {
    "backend_py_total": count_py(BACKEND),
    "frontend_vue_total": count_vue(FRONTEND),
    "frontend_ts_total": count_ts(FRONTEND),
    "largest_vue": largest_files(FRONTEND, "vue", 10),
    "largest_py": largest_files(BACKEND, "py", 10),
}
print(json.dumps(stats, ensure_ascii=False, indent=2))
