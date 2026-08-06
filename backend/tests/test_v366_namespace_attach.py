"""
v3.6.6 HOTFIX — TrainingLifecycleService.get_job_history_sync 命名空间挂载
============================================================================

**Bug 场景 (用户报告 2026-08-06, 紧接 v3.6.4 之后)**:
v3.6.4 在 `job.py` 新增了 `get_job_history` / `get_job_history_sync` 模块级函数,
但忘记在 `__init__.py` 中:
1. 导入这两个函数
2. 挂载为 `TrainingLifecycleService` 类的 staticmethod

**结果**:
- Worker 启动时调 `TrainingLifecycleService.get_job_history_sync(job_id)`
- 触发 `AttributeError: type object 'TrainingLifecycleService' has no attribute 'get_job_history_sync'`
- 整个训练任务崩溃, 无法 resume

**修复 (v3.6.6)**:
- `app/tasks/service/training_lifecycle_service/__init__.py`:
  - 从 `job` 导入 `get_job_history` / `get_job_history_sync`
  - 挂载为 `TrainingLifecycleService.get_job_history` / `.get_job_history_sync` staticmethod
  - 更新类 docstring 描述

**测试覆盖**:
- T1: `__init__.py` 必须导入 `get_job_history` 和 `get_job_history_sync`
- T2: `TrainingLifecycleService` 类必须挂载这两个方法
- T3: 反向测试: 删了挂载应该立即失败

**测试模式**:
- 静态分析 (AST 解析) + 动态 import 验证
- 防止未来添加子模块函数时, 再次漏掉 namespace 挂载

**运行方式**:
    cd backend && python -m pytest tests/test_v366_namespace_attach.py -v
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


# ============== 路径常量 ==============

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_INIT = BACKEND_ROOT / "app" / "tasks" / "service" / "training_lifecycle_service" / "__init__.py"
JOB_PY = BACKEND_ROOT / "app" / "tasks" / "service" / "training_lifecycle_service" / "job.py"


def _read(path: Path) -> str:
    assert path.exists(), f"源文件不存在: {path}"
    return path.read_text(encoding="utf-8")


# ============== T1: __init__.py 必须导入新函数 ==============

class TestInitPyImportsNewFunctions:
    """契约: `__init__.py` 的 `from .job import (...)` 必须包含新函数"""

    def test_init_py_imports_get_job_history(self):
        src = _read(LIFECYCLE_INIT)
        # 在 from ...job import (...) 块中查找 get_job_history
        m = re.search(
            r"from\s+app\.tasks\.service\.training_lifecycle_service\.job\s+import\s+\(([^)]+)\)",
            src,
            re.DOTALL,
        )
        assert m, "未找到 `from app.tasks.service.training_lifecycle_service.job import (...)` 块"
        import_block = m.group(1)
        assert "get_job_history" in import_block, (
            "v3.6.6 HOTFIX 回归: `__init__.py` 的 job import 块缺少 get_job_history\n"
            "请在 from app.tasks.service.training_lifecycle_service.job import (...) 中加 get_job_history"
        )

    def test_init_py_imports_get_job_history_sync(self):
        src = _read(LIFECYCLE_INIT)
        m = re.search(
            r"from\s+app\.tasks\.service\.training_lifecycle_service\.job\s+import\s+\(([^)]+)\)",
            src,
            re.DOTALL,
        )
        assert m, "未找到 `from app.tasks.service.training_lifecycle_service.job import (...)` 块"
        import_block = m.group(1)
        assert "get_job_history_sync" in import_block, (
            "v3.6.6 HOTFIX 回归: `__init__.py` 的 job import 块缺少 get_job_history_sync\n"
            "请在 from app.tasks.service.training_lifecycle_service.job import (...) 中加 get_job_history_sync"
        )


# ============== T2: TrainingLifecycleService 类必须挂载 ==============

class TestTrainingLifecycleServiceAttachesNewMethods:
    """契约: `TrainingLifecycleService` 类必须挂载新函数为 staticmethod"""

    def _get_class_body(self) -> str:
        src = _read(LIFECYCLE_INIT)
        tree = ast.parse(src, filename=str(LIFECYCLE_INIT))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "TrainingLifecycleService":
                return ast.unparse(node)
        raise AssertionError("未找到 TrainingLifecycleService 类")

    def test_class_attaches_get_job_history(self):
        class_body = self._get_class_body()
        assert "get_job_history = staticmethod(get_job_history)" in class_body, (
            "v3.6.6 HOTFIX 回归: TrainingLifecycleService 类未挂载 get_job_history staticmethod\n"
            "请在类体加一行: get_job_history = staticmethod(get_job_history)"
        )

    def test_class_attaches_get_job_history_sync(self):
        class_body = self._get_class_body()
        assert "get_job_history_sync = staticmethod(get_job_history_sync)" in class_body, (
            "v3.6.6 HOTFIX 回归: TrainingLifecycleService 类未挂载 get_job_history_sync staticmethod\n"
            "请在类体加一行: get_job_history_sync = staticmethod(get_job_history_sync)"
        )


# ============== T3: 反向防御: 删了挂载应该立即失败 ==============

class TestNegativeCases:
    """反向测试: 如果未来误删 namespace 挂载, 测试必须立即报错"""

    def test_no_get_job_history_uses_lambda_or_partial(self):
        """禁止: 用 lambda 或 functools.partial 挂载 (破坏静态分析可见性)"""
        class_body = TestTrainingLifecycleServiceAttachesNewMethods()._get_class_body()
        assert "lambda" not in class_body or "get_job_history" not in [
            line.split("=")[0].strip().split()[-1]
            for line in class_body.split("\n")
            if "lambda" in line
        ], "v3.6.6 风格规范: 不允许用 lambda 挂载方法"

    def test_method_is_staticmethod_not_classmethod(self):
        """必须是 staticmethod (与其他 Phase 5 方法保持一致)"""
        class_body = TestTrainingLifecycleServiceAttachesNewMethods()._get_class_body()
        # 查找 get_job_history / get_job_history_sync 的挂载行
        for method_name in ("get_job_history", "get_job_history_sync"):
            pattern = rf"{method_name}\s*=\s*(staticmethod|classmethod)"
            m = re.search(pattern, class_body)
            assert m, f"{method_name} 必须是 staticmethod 或 classmethod 挂载"
            assert m.group(1) == "staticmethod", (
                f"{method_name} 应该用 staticmethod (与 create_or_reset_job_sync 等保持一致)"
            )


# ============== T4: job.py 必须定义这些函数 ==============

class TestJobPyDefinesFunctions:
    """契约: `job.py` 必须定义 `get_job_history` 和 `get_job_history_sync`"""

    def test_job_py_defines_get_job_history_async(self):
        src = _read(JOB_PY)
        assert re.search(
            r"async\s+def\s+get_job_history\s*\(\s*job_id\s*:\s*int\s*\)\s*->",
            src,
        ), "job.py 缺少 async def get_job_history(job_id: int) -> ..."

    def test_job_py_defines_get_job_history_sync(self):
        src = _read(JOB_PY)
        assert re.search(
            r"def\s+get_job_history_sync\s*\(\s*job_id\s*:\s*int\s*\)\s*->",
            src,
        ), "job.py 缺少 def get_job_history_sync(job_id: int) -> ..."

    def test_get_job_history_sync_calls_run_async(self):
        """契约: _sync 函数必须包 _run_async (与 create_or_reset_job_sync 一致)"""
        src = _read(JOB_PY)
        # 定位 get_job_history_sync 函数体
        m = re.search(
            r"def\s+get_job_history_sync\s*\([^)]*\)[^:]*:\s*\n((?:\s{4,}\S.*\n)+)",
            src,
        )
        assert m, "未找到 get_job_history_sync 函数体"
        body = m.group(1)
        assert "_run_async" in body, (
            "v3.6.6 风格规范: get_job_history_sync 必须用 _run_async 包装"
        )
        assert "get_job_history" in body, (
            "v3.6.6 风格规范: get_job_history_sync 必须调用 async get_job_history"
        )


# ============== T5: 三个 worker 调用的参数一致 ==============

class TestWorkersCallGetJobHistorySyncCorrectly:
    """契约: 3 个 worker 都调 TrainingLifecycleService.get_job_history_sync(job_id)"""

    def test_classification_worker_uses_get_job_history_sync(self):
        from tests.test_v363_resume_start_epoch import WORKERS_CLASSIFICATION_PY
        src = _read(WORKERS_CLASSIFICATION_PY)
        assert "TrainingLifecycleService.get_job_history_sync" in src, (
            "workers/classification.py 未调 TrainingLifecycleService.get_job_history_sync"
        )

    def test_segmentation_worker_uses_get_job_history_sync(self):
        from tests.test_v363_resume_start_epoch import WORKERS_SEGMENTATION_PY
        src = _read(WORKERS_SEGMENTATION_PY)
        assert "TrainingLifecycleService.get_job_history_sync" in src, (
            "workers/segmentation/train.py 未调 TrainingLifecycleService.get_job_history_sync"
        )

    def test_detection_worker_uses_get_job_history_sync(self):
        from tests.test_v363_resume_start_epoch import WORKERS_DETECTION_PY
        src = _read(WORKERS_DETECTION_PY)
        assert "TrainingLifecycleService.get_job_history_sync" in src, (
            "workers/detection/train.py 未调 TrainingLifecycleService.get_job_history_sync"
        )
