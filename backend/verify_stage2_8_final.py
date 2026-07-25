"""
Stage 2.8.5 综合验证脚本:
1. py_compile 全部 .py 文件
2. FastAPI 99 路由加载
3. 7 Celery 任务注册
4. AppRegistry 4 apps 注册
5. 旧路径引用扫描
"""
import py_compile
import sys
from pathlib import Path

BACKEND_ROOT = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend")
APP_ROOT = BACKEND_ROOT / "app"


def step1_pycompile() -> bool:
    """Step 1: py_compile 全部 .py 文件"""
    print("\n========== Step 1: py_compile ==========")
    failed = []
    total = 0
    for py_file in APP_ROOT.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        total += 1
        try:
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError as e:
            failed.append((py_file, str(e)))

    print(f"Total .py files: {total}")
    print(f"Failed: {len(failed)}")
    for path, err in failed[:10]:
        print(f"  [FAIL] {path.relative_to(BACKEND_ROOT)}: {err[:100]}")
    return len(failed) == 0


def step2_fastapi_routes() -> bool:
    """Step 2: FastAPI 99 路由加载"""
    print("\n========== Step 2: FastAPI Routes ==========")
    sys.path.insert(0, str(BACKEND_ROOT))
    try:
        from app.main import app
        routes = [r for r in app.routes if hasattr(r, 'path')]
        print(f"Total routes: {len(routes)}")
        if len(routes) >= 99:
            print("[OK] >= 99 routes")
            return True
        print(f"[WARN] Expected 99 routes, got {len(routes)}")
        return False
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return False


def step3_celery_tasks() -> bool:
    """Step 3: 7 Celery 任务注册"""
    print("\n========== Step 3: Celery Tasks ==========")
    sys.path.insert(0, str(BACKEND_ROOT))
    try:
        from app.tasks.workers.celery_app import celery_app
        tasks = list(celery_app.tasks.keys())
        # 过滤掉 celery 内置任务
        user_tasks = [t for t in tasks if not t.startswith("celery.")]
        print(f"Total user tasks: {len(user_tasks)}")
        for t in user_tasks:
            print(f"  - {t}")
        if len(user_tasks) == 7:
            print("[OK] 7 tasks registered")
            return True
        print(f"[WARN] Expected 7 tasks, got {len(user_tasks)}")
        return False
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return False


def step4_app_registry() -> bool:
    """Step 4: AppRegistry 4 apps 注册"""
    print("\n========== Step 4: AppRegistry ==========")
    sys.path.insert(0, str(BACKEND_ROOT))
    try:
        from app.registry import AppRegistry
        apps = list(AppRegistry._apps.keys())
        print(f"Registered apps: {apps}")
        if len(apps) == 4:
            print("[OK] 4 apps registered")
            return True
        print(f"[WARN] Expected 4 apps, got {len(apps)}")
        return False
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return False


def step5_iter_routes() -> bool:
    """Step 5: iter_routes() 输出 14 个 RouteEntry"""
    print("\n========== Step 5: iter_routes() ==========")
    sys.path.insert(0, str(BACKEND_ROOT))
    try:
        from app.registry import AppRegistry
        routes = AppRegistry.iter_routes()
        print(f"Total RouteEntry: {len(routes)}")
        for app_name, entry in routes:
            print(f"  - {app_name:12s} prefix={entry.prefix:20s} routes={len(entry.router.routes)}")
        if len(routes) == 14:
            print("[OK] 14 RouteEntry")
            return True
        print(f"[WARN] Expected 14 RouteEntry, got {len(routes)}")
        return False
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return False


def step6_no_old_paths() -> bool:
    """Step 6: 旧路径不再有引用"""
    print("\n========== Step 6: No old path refs ==========")
    import re
    patterns = [
        r"\bapp\.api\b",
        r"\bapp\.services\b",
        r"\bapp\.model\b",
        r"\bapp\.ml\b",
        r"\bapp\.workers\b",
    ]
    found = 0
    for py_file in APP_ROOT.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        content = py_file.read_text(encoding="utf-8")
        for pattern in patterns:
            matches = re.findall(pattern, content)
            if matches:
                # 排除 __pycache__ 兼容垫片/已经迁移的子目录
                rel = str(py_file.relative_to(BACKEND_ROOT))
                # 这些位置可能还有引用 (兼容垫片文件), 不计入
                if any(x in rel for x in ["database", "common/base_model", "core/deps", "config.py"]):
                    continue
                print(f"  [FOUND] {rel}: {pattern} ({len(matches)}x)")
                found += len(matches)
    if found == 0:
        print("[OK] No old path references")
        return True
    print(f"[FAIL] Found {found} old path references")
    return False


def main():
    results = [
        step1_pycompile(),
        step2_fastapi_routes(),
        step3_celery_tasks(),
        step4_app_registry(),
        step5_iter_routes(),
        step6_no_old_paths(),
    ]
    print("\n========== Summary ==========")
    print(f"Passed: {sum(results)}/{len(results)}")
    for i, (name, ok) in enumerate(zip(
        ["py_compile", "routes", "celery", "registry", "iter_routes", "no_old_paths"],
        results
    )):
        print(f"  {'✓' if ok else '✗'} {name}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
