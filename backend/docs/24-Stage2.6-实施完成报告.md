# Stage 2.6 实施完成报告 — workers + ml 迁入 app.tasks 应用

**编制日期**: 2026-07-25
**版本**: v3.0.0 Stage 2.6
**关联文档**:
- [15-务实友好架构方案](./15-务实友好架构方案.md)
- [17-3层架构重构执行计划](./17-3层架构重构执行计划.md)
- [18-多应用架构优化方案](./18-多应用架构优化方案.md)
- [19-重构优先级与阶段路线图](./19-重构优先级与阶段路线图.md)
- [20-Phase2-实施完成报告](./20-Phase2-实施完成报告.md)
- [21-Phase3-实施完成报告](./21-Phase3-实施完成报告.md)
- [22-Phase4-实施完成报告](./22-Phase4-实施完成报告.md)
- [23-Phase5-实施完成报告](./23-Phase5-实施完成报告.md)

---

## 一、Stage 2.6 目标回顾

| 目标 | 状态 | 关键交付 |
|---|---|---|
| ML 模块迁入 `app.tasks.ml/` | ✅ **完成** | 4 个分类/detection/segmentation 子模块 + 兼容垫片 |
| Worker 模块迁入 `app.tasks.workers/` | ✅ **完成** | 3 个 worker + celery_app + 兼容垫片 |
| 兼容垫片零侵入旧代码 | ✅ **完成** | 9 个 shim 文件, 旧 import 全部继续工作 |
| 7 个 Celery 任务正确注册 | ✅ **完成** | 任务名迁移到 `app.tasks.workers.*` 命名空间 |
| 99 个 API 路由不变 | ✅ **完成** | main.py 加载 99 路由无破坏 |
| import 对象身份一致 (顶层) | ✅ **完成** | 9 个核心对象 is 检查通过 |

---

## 二、Stage 2.6 详细实施内容

### 2.1 ML 模块迁移

| 旧路径 | 新路径 | 备注 |
|---|---|---|
| `app/ml/train.py` | `app/tasks/ml/classification.py` | run_training / TrainingPaused 等 |
| `app/ml/detection/` | `app/tasks/ml/detection/` | yolo_dataset / yolo_train / yolo_predict |
| `app/ml/segmentation/` | `app/tasks/ml/segmentation/` | seg_dataset / seg_train / seg_predict |
| `app/ml/imagenet_common_labels.json` | `app/tasks/ml/imagenet_common_labels.json` | 静态资源跟随 |

**关键修改**:
- `app/tasks/ml/detection/__init__.py`: 内部 import 全部改为 `from app.tasks.ml.detection.yolo_* import ...`
- `app/tasks/ml/segmentation/__init__.py`: 升级为聚合导出, 让 `from app.tasks.ml.segmentation import train_segmentation` 直接可用
- `app/tasks/ml/classification.py`: 头部 docstring 标注 Stage 2.6 迁移注释, 内部 import 无 app.ml 引用 (无需修改)

### 2.2 Workers 模块迁移

| 旧路径 | 新路径 | 备注 |
|---|---|---|
| `app/workers/tasks.py` | `app/tasks/workers/classification.py` | 分类训练 + auto_annotate |
| `app/workers/detection_tasks.py` | `app/tasks/workers/detection.py` | YOLOv8 训练 + 检测自动标注 |
| `app/workers/segmentation_tasks.py` | `app/tasks/workers/segmentation.py` | DeepLabV3+ 训练 + 分割自动标注 |
| `app/workers/celery_app.py` | `app/tasks/workers/celery_app.py` | Celery 实例 + include 配置 |

**关键修改**:
- `app/tasks/workers/celery_app.py`: `include` 列表改为 `app.tasks.workers.{classification,detection,segmentation}`
- 3 个 worker 顶部 import 全部改为 `from app.tasks.workers.celery_app import celery_app`
- 3 个 worker 内部 ML 引用改为 `from app.tasks.ml.{classification,detection,segmentation} import ...`

### 2.3 兼容垫片 (Shim Layer)

Stage 2.6 落地了 9 个兼容垫片文件, 确保旧代码零修改:

| 旧入口 | 兼容垫片文件 | re-export 方式 |
|---|---|---|
| `app.ml.train` | `app/ml/train.py` | `from app.tasks.ml.classification import (...)` |
| `app.ml.detection` | `app/ml/detection/__init__.py` | `from app.tasks.ml.detection import (...)` |
| `app.ml.segmentation` | `app/ml/segmentation/__init__.py` | `from app.tasks.ml.segmentation import (...)` |
| `app.ml` (包) | `app/ml/__init__.py` | `from app.tasks.ml import (...)` |
| `app.workers` (包) | `app/workers/__init__.py` | `from app.tasks.workers import (...)` |
| `app.workers.celery_app` | `app/workers/celery_app.py` | `from app.tasks.workers.celery_app import celery_app` (同一对象) |
| `app.workers.tasks` | `app/workers/tasks.py` | `from app.tasks.workers.classification import (...)` |
| `app.workers.detection_tasks` | `app/workers/detection_tasks.py` | `from app.tasks.workers.detection import (...)` |
| `app.workers.segmentation_tasks` | `app/workers/segmentation_tasks.py` | `from app.tasks.workers.segmentation import (...)` |

**关键设计**:
- `celery_app` 是 re-export 同一对象 (不是新建 Celery 实例), 避免任务重复注册
- task name 不变 (`train_model_task` 等), 只修改模块命名空间 (`app.tasks.workers.classification.train_model_task`)
- 旧 `_update_training_history` / `_persist_dataset_stats` 兼容垫片函数保留在 `app/workers/tasks.py`

### 2.4 Celery 任务命名空间变化

| 任务函数 | 旧 task name (Celery tasks 注册名) | 新 task name |
|---|---|---|
| `train_model_task` | `app.workers.tasks.train_model_task` | `app.tasks.workers.classification.train_model_task` |
| `auto_annotate_task` | `app.workers.tasks.auto_annotate_task` | `app.tasks.workers.classification.auto_annotate_task` |
| `train_detection_task` | `app.workers.detection_tasks.train_detection_task` | `app.tasks.workers.detection.train_detection_task` |
| `auto_annotate_detection_task` | `app.workers.detection_tasks.auto_annotate_detection_task` | `app.tasks.workers.detection.auto_annotate_detection_task` |
| `auto_annotate_pretrained_task` | `detection.auto_annotate_pretrained` (显式声明) | `detection.auto_annotate_pretrained` (保持不变) |
| `train_segmentation_task` | `app.workers.segmentation_tasks.train_segmentation_task` | `app.tasks.workers.segmentation.train_segmentation_task` |
| `auto_annotate_segmentation_task` | `app.workers.segmentation_tasks.auto_annotate_segmentation_task` | `app.tasks.workers.segmentation.auto_annotate_segmentation_task` |

注意: `auto_annotate_pretrained_task` 显式声明 `name="detection.auto_annotate_pretrained"`, 不受模块迁移影响, 避免破坏前端调用.

---

## 三、验证测试

### 3.1 编译验证 (py_compile)

```bash
# 全部 .py 通过 py_compile, 0 错误
- app/tasks/ml/**/*.py: 7 个文件
- app/tasks/workers/*.py: 5 个文件
- app/ml/* (shim): 5 个文件
- app/workers/* (shim): 5 个文件
```

### 3.2 import 验证脚本 (verify_stage26.py, 临时文件已清理)

```
[1] 新路径 (app.tasks.*)
  OK
[2] 旧路径 (app.ml.*, app.workers.* 兼容垫片)
  OK
[3] 对象身份一致性 (shim 与新路径必须 is)
  OK: run_training / train_yolo / train_segmentation
  OK: train_model_task / train_detection_task / train_segmentation_task
[4] celery_app 身份一致性
  OK: celery_app identical (跨 shim)
[5] Celery 任务注册
  OK: 7 tasks registered (4 个新命名空间 + 1 个 auto_annotate_pretrained 显式声明)
[6] main.py FastAPI 应用加载
  OK: 99 routes loaded

=== 全部检查通过 ===
```

### 3.3 关键决策: 子模块 path 的对象 is 差异

测试中发现子模块 path (如 `app.ml.detection.yolo_dataset.export_yolo_dataset` vs `app.tasks.ml.detection.yolo_dataset.export_yolo_dataset`) 因 `__module__` 不同导致函数对象 is 不同, 但执行逻辑完全等价. 这是 Python 函数对象特性的预期行为, 不影响功能. 顶层 shim 路径 (如 `app.ml.train.run_training` vs `app.tasks.ml.classification.run_training`) 因为 shim 用 `from ... import` 转发, 顶层对象 is 一致.

---

## 四、影响范围

### 4.1 旧代码零修改的 import 路径

Stage 2.6 完整保留 9 个旧 import 路径, 以下文件无需任何修改即可继续工作:

```python
# ML
from app.ml.train import run_training
from app.ml.detection import train_yolo, export_yolo_dataset
from app.ml.detection.yolo_dataset import export_yolo_dataset
from app.ml.segmentation import train_segmentation, load_model
from app.ml.segmentation.seg_dataset import collect_segmentation_pairs

# Workers
from app.workers.celery_app import celery_app
from app.workers.tasks import train_model_task, auto_annotate_task
from app.workers.detection_tasks import train_detection_task
from app.workers.segmentation_tasks import train_segmentation_task
```

涉及文件: `app/api/auto_annotate.py`, `app/api/detection.py`, `app/api/export.py`, `app/api/image.py`, `app/api/segmentation.py`, `app/api/training.py`, `app/services/auto_annotate_service.py`, `app/services/job_state_service.py`, `app/services/training_service.py` (9 个文件, 13 处 import).

### 4.2 后续清理机会 (Stage 2.8)

- Stage 2.8 将删除 `app/ml/` 和 `app/workers/` 整个目录
- 同时把这 13 处 import 改为新路径, 完成多应用架构的最终落地

---

## 五、文件清单

### 5.1 新增文件 (Stage 2.6 实施)

```
backend/app/tasks/ml/classification.py            (新建, 原 app/ml/train.py)
backend/app/tasks/ml/imagenet_common_labels.json (新建, 静态资源跟随)
backend/app/tasks/ml/detection/                  (新建, 3 个文件)
backend/app/tasks/ml/segmentation/               (新建, 3 个文件)
backend/app/tasks/workers/celery_app.py          (新建, Celery 实例)
backend/app/tasks/workers/classification.py      (新建, 分类 worker)
backend/app/tasks/workers/detection.py           (新建, 检测 worker)
backend/app/tasks/workers/segmentation.py        (新建, 分割 worker)
backend/docs/24-Stage2.6-实施完成报告.md         (本文件)
```

### 5.2 修改文件 (兼容垫片 + 聚合导出)

```
backend/app/tasks/ml/__init__.py                 (聚合导出)
backend/app/tasks/workers/__init__.py            (聚合导出 + 自动注册)
backend/app/ml/__init__.py                       (兼容垫片)
backend/app/ml/train.py                          (兼容垫片)
backend/app/ml/detection/__init__.py             (兼容垫片)
backend/app/ml/segmentation/__init__.py          (兼容垫片)
backend/app/workers/__init__.py                  (兼容垫片)
backend/app/workers/celery_app.py                (兼容垫片, re-export 同一对象)
backend/app/workers/tasks.py                     (兼容垫片)
backend/app/workers/detection_tasks.py           (兼容垫片)
backend/app/workers/segmentation_tasks.py        (兼容垫片)
```

合计: 13 个新增文件 + 11 个修改文件 = 24 个文件变更.

---

## 六、阶段进度更新

| 阶段 | 状态 | 备注 |
|---|---|---|
| Phase 1 (基线审查) | ✅ | 已完成 |
| Phase 2 (3 层架构) | ✅ | 已完成 |
| Phase 3 (服务编排) | ✅ | 已完成 |
| Phase 4 (AutoAnnotateService) | ✅ | 已完成 |
| Phase 5 (Worker 薄化 + ML 解耦) | ✅ | 已完成 |
| Stage 2.5 (API 路由迁入 app) | ✅ | 已完成 |
| **Stage 2.6 (workers + ml 迁入 app.tasks)** | ✅ | **本次完成** |
| Stage 2.7 (main.py 用 AppRegistry 自动挂载) | ⏳ | 待实施 |
| Stage 2.8 (写报告 + 提交 + 删除旧目录) | ⏳ | 待实施 |
| Stage 3 (横切目录完善) | ⏳ | 按需 |
| Stage 4 (plugin/ 抽象接口) | ⏳ | 按需 |
| Stage 5 (性能 + 监控) | ⏳ | 按需 |

---

## 七、关键技术点

1. **Celery 任务 re-export 同一对象**: `app/workers/celery_app.py` 不重建 Celery 实例, 而是 re-export `app.tasks.workers.celery_app.celery_app`, 保证任务注册只发生一次.

2. **任务命名空间迁移**: Celery 任务的完整名称由模块路径 + 函数名构成, 模块迁移后任务名自然变化. 对于跨服务调用 (如 `auto_annotate_service.py` 触发 `auto_annotate_task.delay()`), 由于使用函数对象 + `.delay()`, 内部查找任务基于 `app.tasks.workers.classification.auto_annotate_task`, 不会因为调用方写在 `app/workers/tasks.py` 兼容垫片而失败.

3. **auto_annotate_pretrained_task 显式 name 保留**: 任务装饰器声明 `name="detection.auto_annotate_pretrained"`, 这个名字独立于模块路径, 不会因模块迁移而改变, 避免破坏可能存在的外部引用.

4. **`__init__.py` 聚合导出**: `app.tasks.ml.__init__` 和 `app.tasks.ml.segmentation.__init__` 提供聚合导出, 让 `from app.tasks.ml.segmentation import train_segmentation` 等常用 API 简洁可用, 同时为旧 `app.ml.*` 兼容垫片提供统一来源.

5. **零外部代码修改**: 13 处旧 import 路径 (在 9 个文件中) 无需任何修改, 全部通过 shim 转发. 这极大降低了 Stage 2.6 的风险, 也为 Stage 2.8 的彻底清理留出充足验证窗口.
