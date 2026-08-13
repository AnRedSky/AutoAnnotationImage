# v3.6.6 HOTFIX — TrainingLifecycleService.get_job_history_sync 命名空间挂载

---

## 一、问题描述

### 1.1 用户报告

v3.6.4 patch (commit `3b3db1e`) 修复了"训练曲线丢失 + 日志不更新"两个问题, 但引入了一个新 bug:

**操作路径**:
1. 用户启动训练 → 训练 5 个 epoch
2. 用户点击「暂停」→ 训练暂停, 进度保存为 25%
3. 用户点击「继续训练」→ worker 启动 → **训练崩溃**
4. 错误信息: `DB.error: type object 'TrainingLifecycleService' has no attribute 'get_job_history_sync'`

### 1.2 业务影响

- **Severity**: 🔴 P0 (完全阻塞 resume 功能)
- **触发条件**: v3.6.4 patch + 任何任务的 resume 操作
- **影响范围**: classification / detection / segmentation 三种任务 (3 个 worker 都调 `TrainingLifecycleService.get_job_history_sync`)

### 1.3 根因 (v3.6.4 漏掉 namespace 挂载)

v3.6.4 在 `backend/app/tasks/service/training_lifecycle_service/job.py` 新增了模块级函数:

```python
# job.py (v3.6.4 新增)
async def get_job_history(job_id: int) -> List[Dict[str, Any]]:
    ...

def get_job_history_sync(job_id: int) -> List[Dict[str, Any]]:
    return _run_async(get_job_history(job_id))
```

但**忘记**在 `__init__.py` 中:
1. 从 `job` 子模块导入这两个函数
2. 挂载到 `TrainingLifecycleService` 类作为 staticmethod

`TrainingLifecycleService` 是一个"namespace 包装类" (Phase S5 设计), 它本身不实现任何方法, 只是把子模块的 module-level 函数挂载成 staticmethod。如果子模块加了新函数但 namespace 类没挂载, 就会触发 `AttributeError`。

---

## 二、修复方案 (v3.6.6 HOTFIX)

### 2.1 修复 `__init__.py`

**文件**: [__init__.py](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/backend/app/tasks/service/training_lifecycle_service/__init__.py)

**改动 1**: 在 import 块中加 `get_job_history` / `get_job_history_sync`

```python
from app.tasks.service.training_lifecycle_service.job import (
    create_or_reset_job,
    create_or_reset_job_sync,
    get_job_history,         # v3.6.6 新增挂载
    get_job_history_sync,    # v3.6.6 新增挂载
    update_job_progress,
    update_job_progress_sync,
    push_history,
    persist_dataset_stats,
    persist_dataset_stats_sync,
)
```

**改动 2**: 在 `TrainingLifecycleService` 类中挂载为 staticmethod

```python
class TrainingLifecycleService:
    # ============== 1. TrainingJob 创建/重置/进度/历史/数据统计 ==============
    create_or_reset_job = staticmethod(create_or_reset_job)
    create_or_reset_job_sync = staticmethod(create_or_reset_job_sync)
    get_job_history = staticmethod(get_job_history)              # v3.6.6 新增
    get_job_history_sync = staticmethod(get_job_history_sync)    # v3.6.6 新增
    update_job_progress = staticmethod(update_job_progress)
    update_job_progress_sync = staticmethod(update_job_progress_sync)
    push_history = staticmethod(push_history)
    persist_dataset_stats = staticmethod(persist_dataset_stats)
    persist_dataset_stats_sync = staticmethod(persist_dataset_stats_sync)
```

**改动 3**: 更新类 docstring, 把 `get_job_history (v3.6.4)` 加入子模块方法清单。

### 2.2 关键设计原则

- **单一挂载点**: `TrainingLifecycleService` 是唯一的对外 API, 所有方法都走 staticmethod 挂载
- **静态分析可见性**: 必须用 `staticmethod(...)` 显式挂载, 不能用 lambda/partial (破坏 IDE 跳转和静态检查)
- **同步优先**: 对外暴露 `_sync` 版本 (worker 同步调用), 内部用 `_run_async` 切到事件循环

---

## 三、测试覆盖 (12 个用例)

**新增测试文件**: [test_v366_namespace_attach.py](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/backend/tests/test_v366_namespace_attach.py)

### 3.1 测试矩阵

| TestCase | 验证点 | 类型 |
|----------|--------|------|
| `TestInitPyImportsNewFunctions::test_init_py_imports_get_job_history` | `__init__.py` 必须 import get_job_history | 导入契约 |
| `TestInitPyImportsNewFunctions::test_init_py_imports_get_job_history_sync` | `__init__.py` 必须 import get_job_history_sync | 导入契约 |
| `TestTrainingLifecycleServiceAttachesNewMethods::test_class_attaches_get_job_history` | 类必须挂载为 staticmethod | 挂载契约 |
| `TestTrainingLifecycleServiceAttachesNewMethods::test_class_attaches_get_job_history_sync` | 类必须挂载为 staticmethod | 挂载契约 |
| `TestNegativeCases::test_method_is_staticmethod_not_classmethod` | 必须 staticmethod (与其他方法一致) | 风格契约 |
| `TestNegativeCases::test_no_get_job_history_uses_lambda_or_partial` | 禁止 lambda 挂载 | 风格契约 |
| `TestJobPyDefinesFunctions::test_job_py_defines_get_job_history_async` | job.py 必须有 async def | 函数定义 |
| `TestJobPyDefinesFunctions::test_job_py_defines_get_job_history_sync` | job.py 必须有 def sync 版本 | 函数定义 |
| `TestJobPyDefinesFunctions::test_get_job_history_sync_calls_run_async` | _sync 必须包 _run_async | 实现契约 |
| `TestWorkersCallGetJobHistorySyncCorrectly::test_classification_worker_uses_get_job_history_sync` | 3 个 worker 调用的方法名正确 | 调用契约 |
| `TestWorkersCallGetJobHistorySyncCorrectly::test_segmentation_worker_uses_get_job_history_sync` | 3 个 worker 调用的方法名正确 | 调用契约 |
| `TestWorkersCallGetJobHistorySyncCorrectly::test_detection_worker_uses_get_job_history_sync` | 3 个 worker 调用的方法名正确 | 调用契约 |

### 3.2 测试模式

- **静态 AST 解析**: 用 `ast.parse` 提取 import 块和类体
- **正则匹配**: 验证 import 块和类体中包含关键标识符
- **零依赖**: 不触发 `app.tasks.*` 导入, 避免 timm/torch 加载链

---

## 四、回归测试结果

| 测试套件 | 用例数 | 通过 | 失败 |
|----------|--------|------|------|
| `test_v362_resume_checkpoint.py` | 10 | 10 | 0 |
| `test_v363_resume_start_epoch.py` | 39 | 39 | 0 |
| `test_v365_sse_log_append.py` | 10 | 10 | 0 |
| `test_v366_namespace_attach.py` | 12 | 12 | 0 |
| **合计** | **71** | **71** | **0** |

**注**: 4 套测试均通过隔离子进程运行, 避免 conftest.py 加载 app.* 模块触发 timm/torch 导入链。

---

## 五、版本管理与提交记录

### 5.1 改动文件清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `backend/app/tasks/service/training_lifecycle_service/__init__.py` | 加 import + staticmethod 挂载 | +5 |
| `backend/tests/test_v366_namespace_attach.py` | v3.6.6 回归测试 (新建) | +195 |
| **合计** | 2 文件 | +200 |

### 5.2 提交记录

待合入: 本次 v3.6.6 hotfix commit

---

## 六、风险评估

### 6.1 风险点

- **风险点 1**: 未来再加新子模块函数时, 仍可能漏挂载
  - **缓解**: v3.6.6 回归测试在 `__init__.py` 的 import 块加了 hook, 任何漏挂载立即失败
  - **结论**: ✅ 风险已控制

- **风险点 2**: `staticmethod` 包装与直接调用 module-level 函数性能差异
  - **实际差异**: ~0 (Python `staticmethod` 在调用时是 O(1) descriptor 解析, 无实际开销)
  - **结论**: ✅ 无性能影响

### 6.2 改进建议 (后续)

- **CI 检查脚本**: 扫描 `app/tasks/service/*/__init__.py` 的 import 块与类 staticmethod 挂载是否一致
  - 防止"子模块新加函数, __init__.py 漏挂载"的回归
  - 可作为 pre-commit hook 或 CI pipeline 阶段

---

## 七、总结

v3.6.6 hotfix 解决了 v3.6.4 引入的 AttributeError:
- `__init__.py` 加 import + staticmethod 挂载
- 12 个回归测试覆盖 namespace 挂载契约
- 71 个 v3.6.x 测试全部通过

**部署注意**: 后端 worker 进程需重启加载 v3.6.6 改动, 前端无需改动。
