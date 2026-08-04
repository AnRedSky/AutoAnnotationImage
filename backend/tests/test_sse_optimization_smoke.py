"""
冒烟测试: 直接 import 修改过的两个文件, 不经过 conftest/app.main
避免 fastapi 0.116 与项目里旧 APIRouter 的兼容性问题
"""
import sys
import traceback

print("=" * 60)
print("  SSE 优化冒烟测试 (v3.5.0 Phase T6)")
print("=" * 60)

# ============== 1. 检查修改后的两个模块能正确编译 ==============
print("\n[1/5] Python 字节码编译检查")
import py_compile
try:
    py_compile.compile("app/tasks/service/job_state_service.py", doraise=True)
    print("  ✓ job_state_service.py 编译通过")
except Exception as e:
    print(f"  ✗ job_state_service.py 编译失败: {e}")
    sys.exit(1)

try:
    py_compile.compile("app/tasks/api/training/progress.py", doraise=True)
    print("  ✓ progress.py 编译通过")
except Exception as e:
    print(f"  ✗ progress.py 编译失败: {e}")
    sys.exit(1)

# ============== 2. 静态分析: 新增方法/字段是否齐全 ==============
print("\n[2/5] 静态检查: JobStateService 新增 API")
import ast
src = open("app/tasks/service/job_state_service.py", encoding="utf-8").read()
tree = ast.parse(src)

# 收集所有 JobStateService 类内的 static method 名
def _is_staticmethod(node: ast.FunctionDef) -> bool:
    for d in node.decorator_list:
        # @staticmethod
        if isinstance(d, ast.Name) and d.id == "staticmethod":
            return True
        # @staticmethod(...)
        if isinstance(d, ast.Call):
            for arg in d.args:
                if isinstance(arg, ast.Name) and arg.id == "staticmethod":
                    return True
    return False


service_methods = set()
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == "JobStateService":
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and _is_staticmethod(item):
                service_methods.add(item.name)
            elif isinstance(item, ast.AsyncFunctionDef) and _is_staticmethod(item):
                service_methods.add(item.name)

expected_in_service = [
    "get_snapshot",
    "get_snapshot_with_fresh_db",
    "_make_db_version",
    "_snapshot_to_cache_dict",
    "_snapshot_from_cache_dict",
    "get_snapshot_with_cache",
    "invalidate_snapshot_cache",
]
for m in expected_in_service:
    if m in service_methods:
        print(f"  ✓ JobStateService.{m} 存在")
    else:
        print(f"  ✗ JobStateService.{m} 缺失 (已找到: {sorted(service_methods)})")
        sys.exit(1)

# ============== 3. 静态分析: progress.py 改用 get_snapshot_with_cache ==============
print("\n[3/5] 静态检查: progress.py 改用新 API")
progress_src = open("app/tasks/api/training/progress.py", encoding="utf-8").read()
if "get_snapshot_with_cache" in progress_src:
    print("  ✓ progress.py 已改用 get_snapshot_with_cache")
else:
    print("  ✗ progress.py 仍用 get_snapshot_with_fresh_db")
    sys.exit(1)

# 验证旧的 SSE_POLL_INTERVAL 常量已被新常量替代
if "SSE_POLL_INTERVAL =" in progress_src and "SSE_POLL_INTERVAL_PROGRESS" not in progress_src:
    print("  ✗ progress.py 仍用旧的 SSE_POLL_INTERVAL")
    sys.exit(1)
if "SSE_POLL_INTERVAL_PROGRESS" in progress_src and "SSE_POLL_INTERVAL_PENDING" in progress_src:
    print("  ✓ progress.py 新增 SSE_POLL_INTERVAL_PROGRESS / _PENDING 常量")

# 验证 db_version 逻辑存在
if "_db_version" in progress_src and "last_db_version" in progress_src:
    print("  ✓ progress.py 已实现 db_version 跳过推送 (方案 3)")
else:
    print("  ✗ progress.py 缺失 db_version 跳过逻辑")
    sys.exit(1)

# 验证自适应 sleep
if 'state == "PENDING"' in progress_src and "SSE_POLL_INTERVAL_PENDING" in progress_src:
    print("  ✓ progress.py 已实现 PENDING 状态 5s 降频 (方案 1)")
else:
    print("  ✗ progress.py 缺失 PENDING 状态降频")
    sys.exit(1)

# ============== 4. 验证 redis_client 引用 ==============
print("\n[4/5] 静态检查: Redis 客户端引用")
if "from app.database.redis import redis_client" in src:
    print("  ✓ job_state_service.py 已 import redis_client")
else:
    print("  ✗ job_state_service.py 缺失 redis_client 引用")
    sys.exit(1)

if "asyncio.to_thread(redis_client.get" in src:
    print("  ✓ 使用 asyncio.to_thread 包装同步 Redis 调用 (避免阻塞 event loop)")
if "asyncio.to_thread(redis_client.setex" in src:
    print("  ✓ 使用 asyncio.to_thread 包装 setex 写缓存")
if "asyncio.to_thread(redis_client.delete" in src:
    print("  ✓ 使用 asyncio.to_thread 包装 delete 失效缓存")

# ============== 5. 单元测试: db_version 行指纹 ==============
print("\n[5/5] 运行 db_version 行指纹单元测试")
import subprocess
result = subprocess.run(
    [sys.executable, "tests/test_db_version_logic.py"],
    capture_output=True, text=True, timeout=30,
)
print(result.stdout)
if result.returncode != 0:
    print(f"  ✗ 单元测试失败 (exit={result.returncode})")
    sys.exit(1)

print()
print("=" * 60)
print("  ALL SMOKE TESTS PASSED ✓")
print("=" * 60)
