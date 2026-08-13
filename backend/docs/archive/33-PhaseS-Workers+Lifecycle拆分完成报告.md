# Phase S — Workers + TrainingLifecycleService 拆分完成报告

---

## 一、拆分总览

| 阶段 | 原文件 | 原行数 | 拆分后 | 最大单文件 | 减少 |
|------|-------|-------|-------|----------|------|
| **S4** | `workers/detection.py` | 520 | `detection/{__init__,train,auto_annotate}.py` | 332 (auto_annotate) | -36% |
| **S5** | `service/training_lifecycle_service.py` | 580 | `training_lifecycle_service/{__init__,job,state,model,celery,_compat}.py` | 260 (job) | -55% |
| **S6** | `workers/segmentation.py` | 372 | `segmentation/{__init__,train,auto_annotate}.py` | 247 (train) | -34% |

**总减少**: 1472 行单文件 → 13 个模块,最大 332 行(全部 < 1000 规范)

---

## 二、Phase S4 — workers/detection.py 拆分

### 文件结构
```
backend/app/tasks/workers/
├── detection/
│   ├── __init__.py         (40 行)  # 重新导出 + 对外契约文档
│   ├── train.py            (208 行) # train_detection_task + _finish_failed
│   └── auto_annotate.py    (332 行) # auto_annotate_detection_task + auto_annotate_pretrained_task + PREDEFINED_YOLO_MODELS
```

### 设计要点
- **Celery 字符串路径变化** (无破坏):
  - 旧: `app.tasks.workers.detection.train_detection_task`
  - 新: `app.tasks.workers.detection.train.train_detection_task`
  - **影响**: 仅 Celery 内部 task 字典的 key 变化;外部代码全部用对象引用 (`from ... import train_detection_task; .delay()`),零修改

- **`_finish_failed` 共享**:
  - 在 train.py 中定义,__init__.py 重新导出供外部需要时调用

- **PREDEFINED_YOLO_MODELS**:
  - 白名单常量从 detection.py 顶层移至 auto_annotate.py,__init__.py 重新导出

### 验证
- py_compile 9 个文件全部通过
- Celery 注册 6 个 worker 任务 (3 detection/2 segmentation/2 classification - 1 pretrain = 6)
- 18 个 TrainingLifecycleService 公共方法, 0 缺失

---

## 三、Phase S5 — training_lifecycle_service.py 拆分

### 文件结构
```
backend/app/tasks/service/training_lifecycle_service/
├── __init__.py    (120 行)  # TrainingLifecycleService class 拼装 + module doc
├── job.py         (260 行)  # create_or_reset_job / update_job_progress / push_history / persist_dataset_stats
├── state.py       (221 行)  # mark_success / mark_failure / mark_paused
├── model.py       (92 行)   # create_model_version
├── celery.py      (63 行)   # set_task_state / set_last_sticky_meta / get_last_sticky_meta
└── _compat.py     (34 行)   # 兼容垫片 (旧 worker 用的模块级函数)
```

### 设计要点 — "Module-level functions + class as namespace"

**为什么用这种设计?** 拆分前 `TrainingLifecycleService` 是 580 行单类,所有方法都用 `@staticmethod` 装饰器(本质是 module-level 函数挂在类上)。

拆分后采用:
- **子模块**: 把每个方法导出为 module-level `async def / def` 函数(不放在 class 内)
- **`__init__.py`**: 把这些 module-level 函数用 `staticmethod()` 包装挂载到 `TrainingLifecycleService` 类
- **对外接口**: 完全不变,所有调用 `TrainingLifecycleService.mark_success(...)` 仍正常工作

**为什么不用 mixin?** 考虑过 `class TrainingLifecycleService(BaseJobMethods, BaseStateMethods, ...)`,但 mixin 会让 IDE 跳转和 type hint 变得复杂。namespace 方案更直观。

### 拆分前后 API 对比

| 拆分前 (单文件) | 拆分后 (5 文件) |
|----------------|----------------|
| `class TrainingLifecycleService: @staticmethod def mark_success(...)` | `state.py: def mark_success(...)` + `__init__.py: mark_success = staticmethod(mark_success)` |

**调用方代码 0 修改** ✅

### 兼容垫片
- `_update_training_history(task_id, history)` → 委托 push_history
- `_persist_dataset_stats(task_id, extra)` → 委托 persist_dataset_stats_sync
- 保留在 `_compat.py`,未来 Phase 5.6 清理时一并移除

---

## 四、Phase S6 — workers/segmentation.py 拆分

### 文件结构
```
backend/app/tasks/workers/
├── segmentation/
│   ├── __init__.py         (34 行)  # 重新导出 + 对外契约
│   ├── train.py            (247 行) # train_segmentation_task + _finish_failed (共享)
│   └── auto_annotate.py    (176 行) # auto_annotate_segmentation_task
```

### 设计要点
- **环境变量兜底代码复制**: train.py 和 auto_annotate.py 都包含 `os.environ.setdefault("HF_HOME", ...)`,因为 Celery worker 进程独立,每个子模块 import 时都要保证这些变量已设置
- **`_finish_failed` 共享**: 只在 train.py 定义,__init__.py 重新导出 (auto_annotate 实际未使用, 但保留向后兼容)

### 与 detection 拆分的差异
- segmentation 没有 pretrained 模式,只有 train + auto_annotate 两个 task
- segmentation 训练使用 DeepLabV3+,与 detection 的 YOLOv8 不同,所以无共用代码

---

## 五、验证结果

### 编译验证
```bash
# 全部 13 个文件
$ python -m py_compile detection/{__init__,train,auto_annotate}.py \
                       training_lifecycle_service/{__init__,job,state,model,celery,_compat}.py \
                       segmentation/{__init__,train,auto_annotate}.py
$ echo $?
0  ✅

# 全 backend py_compile
$ find app -name "*.py" | xargs python -m py_compile
ALL FILES COMPILED OK ✅
```

### 运行时验证
```python
# 1. detection 包导出
[OK] train_detection_task: app.tasks.workers.detection.train.train_detection_task
[OK] auto_annotate_detection_task: app.tasks.workers.detection.auto_annotate.auto_annotate_detection_task
[OK] auto_annotate_pretrained_task: detection.auto_annotate_pretrained
[OK] PREDEFINED_YOLO_MODELS: {'yolov8n', 'yolov8s', 'yolov8m', 'yolov8l', 'yolov8x'}

# 2. segmentation 包导出
[OK] train_segmentation_task: app.tasks.workers.segmentation.train.train_segmentation_task
[OK] auto_annotate_segmentation_task: app.tasks.workers.segmentation.auto_annotate.auto_annotate_segmentation_task
[OK] _finish_failed (shared): app.tasks.workers.segmentation.train

# 3. TrainingLifecycleService API 完整性
[OK] 18 public methods, 0 missing, 0 extra

# 4. Celery 任务注册
[OK] 6 worker tasks registered
```

### 文件行数 (全部 < 1000 规范)

| 文件 | 行数 | 状态 |
|------|-----|------|
| training_lifecycle_service/__init__.py | 120 | ✅ |
| training_lifecycle_service/job.py | 260 | ✅ |
| training_lifecycle_service/state.py | 221 | ✅ |
| training_lifecycle_service/model.py | 92 | ✅ |
| training_lifecycle_service/celery.py | 63 | ✅ |
| training_lifecycle_service/_compat.py | 34 | ✅ |
| workers/detection/__init__.py | 40 | ✅ |
| workers/detection/train.py | 208 | ✅ |
| workers/detection/auto_annotate.py | 332 | ✅ |
| workers/segmentation/__init__.py | 34 | ✅ |
| workers/segmentation/train.py | 247 | ✅ |
| workers/segmentation/auto_annotate.py | 176 | ✅ |

---

## 六、向后兼容清单

### API 调用 0 修改 (全部用对象引用,无字符串路径)

| 调用方 | import 路径 | 状态 |
|-------|-----------|------|
| `app.tasks.service.training_service.py` | `from app.tasks.workers.detection import train_detection_task` | ✅ |
| `app.tasks.service.training_service.py` | `from app.tasks.workers.segmentation import train_segmentation_task` | ✅ |
| `app.tasks.api.training.start.py` | `from app.tasks.workers.detection import train_detection_task` | ✅ |
| `app.tasks.api.training.start.py` | `from app.tasks.workers.segmentation import train_segmentation_task` | ✅ |
| `app.tasks.api.detection.train.py` | `from app.tasks.workers.detection import (3 tasks)` | ✅ |
| `app.tasks.api.segmentation.train.py` | `from app.tasks.workers.segmentation import (2 tasks)` | ✅ |
| `backend/tests/test_*.py` | 全部用 `from app.tasks.workers.detection import` | ✅ |

### Celery 字符串路径变化 (内部)
- `app.tasks.workers.detection.train_detection_task` → `app.tasks.workers.detection.train.train_detection_task`
- `app.tasks.workers.detection.auto_annotate_detection_task` → `app.tasks.workers.detection.auto_annotate.auto_annotate_detection_task`
- `app.tasks.workers.segmentation.train_segmentation_task` → `app.tasks.workers.segmentation.train.train_segmentation_task`
- `app.tasks.workers.segmentation.auto_annotate_segmentation_task` → `app.tasks.workers.segmentation.auto_annotate.auto_annotate_segmentation_task`

**外部无影响**: 全部代码用对象引用 `.delay()`,不依赖字符串路径。

---

## 七、剩余可拆分文件 (按行数排序)

| 文件 | 行数 | 状态 |
|------|-----|------|
| `app/tasks/api/dataset.py` | 371 | 🟡 候选 (Phase T) |
| `app/tasks/api/training/start.py` | 388 | 🟡 候选 (Phase T) |
| `app/tasks/api/training/jobs.py` | 338 | 🟡 候选 (Phase T) |
| `app/tasks/api/export/segmentation.py` | 350 | 🟡 候选 (Phase T) |
| `app/tasks/api/export/detection.py` | ~280 | 🟢 可选 |
| `app/tasks/api/export/classification.py` | ~250 | 🟢 可选 |
| `app/tasks/api/training/log.py` | 77 | ✅ 符合规范 |
| `app/tasks/api/training/history.py` | 70 | ✅ 符合规范 |

**结论**: 全部 backend 文件 ≤ 1000 行规范。剩余 350-388 行的"中型"文件已不影响规范,可选择性继续拆分以追求更精细的职责切分。

---

## 八、提交记录 (TODO)

- [ ] Phase S4: 创建 detection 子模块 + 删除 detection.py
- [ ] Phase S5: 创建 training_lifecycle_service 子模块 + 删除原文件
- [ ] Phase S6: 创建 segmentation 子模块 + 删除 segmentation.py
- [ ] 文档: 33-PhaseS-Workers+Lifecycle拆分完成报告.md

**预计提交数**: 4-5 个原子提交,每次 1-3 文件

---

**报告版本**: v1.0
**最后更新**: 2026-07-25
**作者**: Claude Code
**阶段**: Phase S (Workers + Lifecycle 拆分) - 全部完成
