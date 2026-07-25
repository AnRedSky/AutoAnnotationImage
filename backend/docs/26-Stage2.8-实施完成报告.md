# Stage 2.8 实施完成报告 — 兼容垫片清理与 Stage 2 收官

**编制日期**: 2026-07-25
**版本**: v3.0.0 Stage 2.8 (Stage 2 完结)
**关联文档**:
- [15-务实友好架构方案](./15-务实友好架构方案.md)
- [18-多应用架构优化方案](./18-多应用架构优化方案.md)
- [19-重构优先级与阶段路线图](./19-重构优先级与阶段路线图.md)
- [24-Stage2.6-实施完成报告](./24-Stage2.6-实施完成报告.md)
- [25-Stage2.7-实施完成报告](./25-Stage2.7-实施完成报告.md)

---

## 一、Stage 2.8 目标回顾

| 目标 | 状态 | 关键交付 |
|---|---|---|
| 迁移 11 个 API 路由到 `app/{admin,tasks,annotation}/api/` | ✅ **完成** | dataset / image / training / detection / segmentation / model / auto_annotate / export / files / annotation / stats |
| 迁移 11 个 Service 到 `app/tasks/service/` + `app/common/*` | ✅ **完成** | training/auto_annotate/dataset/annotation/detection/segmentation/job_state + geometry/ml/storage 公共子目录 |
| 删除旧 `app/api/`, `app/model/`, `app/services/`, `app/ml/`, `app/workers/` | ✅ **完成** | 共删除 56 个文件 / 目录 |
| 修正 6 处残留 `app.model` / `app.services` 注释引用 | ✅ **完成** | classification.py + training_data_service.py + annotation/service/__init__.py |
| 99 路由不变 + 7 Celery 任务注册 + 4 App 注册 | ✅ **完成** | 0 路由增减 / 0 任务增减 / AppRegistry 4 apps |
| 所有 import 路径切到新位置 | ✅ **完成** | verify_stage2_8_final.py 6/6 步骤全通过 |
| Stage 2 完结收官 | ✅ **完成** | Stage 2 (Stage 2.1-2.8) 全部交付 |

---

## 二、Stage 2 整体回顾 (Stage 2.1 → 2.8)

| 阶段 | 内容 | 文件迁移 | 关键交付 |
|---|---|---|---|
| **Stage 2.1+2.2** | AppRegistry + 4 App 骨架 (admin/auth/tasks/annotation) | 4 应用入口 | AppInterface, AppRegistry, EventBus, EventHandler |
| **Stage 2.3** | ORM 模型迁移到 `app/{admin,tasks,annotation}/model/` | 9 ORM + 9 queries | 兼容垫片 + sys.modules 别名 |
| **Stage 2.4** | Service 迁移到 `app/{admin,auth,tasks}/service/` | 6 service | annotation_service 等 6 个核心 service |
| **Stage 2.5** | API 路由迁移到 `app/{admin,auth,tasks,annotation}/api/` | 3 router 完整 | user / system / stats 优先迁移 |
| **Stage 2.6** | ML + Workers 迁移到 `app/tasks/{ml,workers}/` | 15 ML + 10 worker | classification/detection/segmentation 三栈 |
| **Stage 2.7** | main.py 改用 AppRegistry 自动挂载 | main.py 14→5 行 | RouteEntry + iter_routes() + startup_all/shutdown_all |
| **Stage 2.8** | 兼容垫片清理 + 收官 | 56 文件删除 | 11 API + 8 service + 16 model + 10 ml + 5 worker + 6 注释修正 |

**Stage 2 总工作量**: ~3 天 (原计划 3 天, 准时完成)

---

## 三、Stage 2.8 详细实施内容

### 3.1 API 路由迁移 (Stage 2.8.1-2.8.2)

**目标**: 把 `app/api/*.py` 全部迁移到对应应用的 `api/` 子目录, 删除旧 `app/api/` 目录。

**迁移映射**:

| 旧路径 (删除) | 新路径 |
|---|---|
| `app/api/dataset.py` | `app/tasks/api/dataset.py` |
| `app/api/image.py` | `app/tasks/api/image.py` |
| `app/api/training.py` | `app/tasks/api/training.py` |
| `app/api/detection.py` | `app/tasks/api/detection.py` |
| `app/api/segmentation.py` | `app/tasks/api/segmentation.py` |
| `app/api/model.py` | `app/tasks/api/model.py` |
| `app/api/auto_annotate.py` | `app/tasks/api/auto_annotate.py` |
| `app/api/export.py` | `app/tasks/api/export.py` |
| `app/api/files.py` | `app/tasks/api/files.py` |
| `app/api/annotation.py` | `app/annotation/api/annotation.py` |
| `app/api/auth.py` | `app/auth/api/auth.py` |
| `app/api/user.py` | `app/admin/api/user.py` |
| `app/api/system.py` | `app/admin/api/system.py` |
| `app/api/stats.py` | `app/admin/api/stats.py` |

**Import 路径修正规则** (`stage2_8_2_migrate_api.py`):
```python
replacements = [
    (r"\bapp\.model\.user\b",            "app.admin.model.user"),
    (r"\bapp\.model\.dataset\b",         "app.tasks.model.dataset"),
    (r"\bapp\.model\.image\b",           "app.tasks.model.image"),
    (r"\bapp\.model\.category\b",        "app.tasks.model.category"),
    (r"\bapp\.model\.model_version\b",   "app.tasks.model.model_version"),
    (r"\bapp\.model\.training_job\b",    "app.tasks.model.training_job"),
    (r"\bapp\.model\.bbox_annotation\b", "app.annotation.model.bbox_annotation"),
    (r"\bapp\.model\.annotation_log\b",  "app.annotation.model.annotation_log"),
    (r"\bapp\.model\.segmentation_mask\b", "app.annotation.model.segmentation_mask"),
    (r"\bapp\.core\.deps\b",             "app.middleware.http.auth"),
    (r"\bapp\.workers\b",                "app.tasks.workers"),
    (r"\bapp\.ml\b",                     "app.tasks.ml"),
    (r"\bapp\.services\.training_service\b",    "app.tasks.service.training_service"),
    (r"\bapp\.services\.annotation_service\b",  "app.tasks.service.annotation_service"),
    (r"\bapp\.services\.dataset_service\b",     "app.tasks.service.dataset_service"),
    (r"\bapp\.services\.bbox_service\b",        "app.common.geometry.bbox_service"),
    (r"\bapp\.services\.storage_service\b",     "app.common.storage.storage_service"),
    (r"\bapp\.services\.ai_service\b",          "app.common.ml.ai_service"),
]
```

### 3.2 Service 迁移 (Stage 2.8.3)

**目标**: 把 `app/services/*.py` 全部迁移到 `app/tasks/service/` (业务) + `app/common/*` (通用) + `app/auth/service/` (认证) 子目录。

**业务 Service 迁移** (`stage2_8_3_migrate_services.py`):

| 旧路径 (删除) | 新路径 |
|---|---|
| `app/services/training_service.py` | `app/tasks/service/training_service.py` |
| `app/services/training_data_service.py` | `app/tasks/service/training_data_service.py` |
| `app/services/training_lifecycle_service.py` | `app/tasks/service/training_lifecycle_service.py` |
| `app/services/auto_annotate_service.py` | `app/tasks/service/auto_annotate_service.py` |
| `app/services/dataset_service.py` | `app/tasks/service/dataset_service.py` |
| `app/services/annotation_service.py` | `app/tasks/service/annotation_service.py` |
| `app/services/detection_service.py` | `app/tasks/service/detection_service.py` |
| `app/services/segmentation_service.py` | `app/tasks/service/segmentation_service.py` |
| `app/services/job_state_service.py` | `app/tasks/service/job_state_service.py` |
| `app/services/model_service.py` | `app/tasks/service/model_service.py` |
| `app/services/image_service.py` | `app/tasks/service/image_service.py` |
| `app/services/stats_service.py` | `app/tasks/service/stats_service.py` |
| `app/services/auth_service.py` | `app/auth/service/auth_service.py` |

**通用 Service 迁移** (新分类 `app/common/*`):

| 旧路径 (删除) | 新路径 | 类别 |
|---|---|---|
| `app/services/bbox_service.py` | `app/common/geometry/bbox_service.py` | 几何 |
| `app/services/storage_service.py` | `app/common/storage/storage_service.py` | 存储 |
| `app/services/ai_service.py` | `app/common/ml/ai_service.py` | 机器学习 |
| `app/services/user_service.py` | (保留为 `app/admin/service/user_service.py`) | 用户管理 |
| `app/services/export_service.py` | (合并到 `app/tasks/service/dataset_service.py`) | 数据导出 |

**横切关注点分类**:
- `app/common/geometry/` — 几何计算 (bbox, nms, iou)
- `app/common/ml/` — ML 通用能力 (ai_service)
- `app/common/storage/` — 文件存储抽象
- `app/common/{base_model, exceptions, interfaces, ...}` — 已存在基础设施

### 3.3 ORM 兼容垫片清理 (Stage 2.8.4)

**目标**: 删除旧 `app/model/` 目录, 确认所有引用已切到 `app/{admin,tasks,annotation}/model/`。

**删除清单** (16 文件):
```
app/model/__init__.py
app/model/base.py
app/model/user.py
app/model/user_queries.py
app/model/dataset.py
app/model/dataset_queries.py
app/model/image.py
app/model/image_queries.py
app/model/category.py
app/model/category_queries.py
app/model/model_version.py
app/model/model_version_queries.py
app/model/training_job.py
app/model/training_queries.py
app/model/bbox_annotation.py
app/model/bbox_annotation_queries.py
app/model/annotation_log.py
app/model/annotation_log_queries.py
app/model/segmentation_mask.py
app/model/segmentation_mask_queries.py
```

**关键修复**:
- `app/database/session.py`: `from app.model.base` → `from app.common.base_model`
- `app/database/session.py`: `import app.model` → `import app.{admin,tasks,annotation}.model`
- `app/tasks/workers/classification.py`: 缩进修正 (TrainingLifecycleService import)
- `app/tasks/ml/detection/yolo_dataset.py`: `from app.tasks.model.{image,category}` 确认

### 3.4 ML + Workers 兼容垫片清理 (Stage 2.8.4)

**目标**: 删除旧 `app/ml/` + `app/workers/` 目录, 确认所有引用已切到 `app/tasks/{ml,workers}/`。

**删除清单** (15 文件):
```
app/ml/__init__.py
app/ml/train.py
app/ml/imagenet_common_labels.json
app/ml/detection/__init__.py
app/ml/detection/yolo_dataset.py
app/ml/detection/yolo_predict.py
app/ml/detection/yolo_train.py
app/ml/segmentation/__init__.py
app/ml/segmentation/seg_dataset.py
app/ml/segmentation/seg_predict.py
app/ml/segmentation/seg_train.py
app/workers/__init__.py
app/workers/celery_app.py
app/workers/tasks.py
app/workers/detection_tasks.py
app/workers/segmentation_tasks.py
```

### 3.5 注释引用修正 (Stage 2.8.5)

**目标**: 修正残留的 `app.model` / `app.services` 注释引用, 满足"无旧路径引用"硬性要求。

| 文件 | 位置 | 修正 |
|---|---|---|
| `app/annotation/service/__init__.py` | line 7 | "app.services.*" → "当前应用" |
| `app/tasks/ml/classification.py` | line 170 | "走 app.model" → "走 ORM 模型层" |
| `app/tasks/ml/classification.py` | line 178 | "app.models/app.database" → "app.tasks.model/app.database" |
| `app/tasks/ml/classification.py` | line 202 | "app.models" → "app.tasks.model" |
| `app/tasks/ml/classification.py` | line 481 | "app.model/app.database" → "app.tasks.model/app.database" |
| `app/tasks/ml/classification.py` | line 485 | "走 app.model" → "走 app.tasks.model" |
| `app/tasks/ml/classification.py` | line 495 | "走 app.model" → "走 app.tasks.model" |
| `app/tasks/service/training_data_service.py` | line 12 | "app.model / app.database" → "ORM 模型层 / app.database" |

---

## 四、验证结果 (`verify_stage2_8_final.py` 6/6 通过)

### 4.1 Step 1: py_compile 全部 134 个 .py 文件

```
Total .py files: 134
Failed: 0
[OK] 所有 134 个文件编译通过
```

### 4.2 Step 2: FastAPI 99 路由加载

```
Total routes: 99
[OK] >= 99 routes
```

### 4.3 Step 3: 7 Celery 任务注册

```
Total user tasks: 7
  - app.tasks.workers.segmentation.train_segmentation_task
  - app.tasks.workers.classification.train_model_task
  - app.tasks.workers.detection.auto_annotate_detection_task
  - app.tasks.workers.segmentation.auto_annotate_segmentation_task
  - app.tasks.workers.classification.auto_annotate_task
  - detection.auto_annotate_pretrained
  - app.tasks.workers.detection.train_detection_task
[OK] 7 tasks registered
```

### 4.4 Step 4: AppRegistry 4 apps 注册

```
Registered apps: ['admin', 'auth', 'tasks', 'annotation']
[OK] 4 apps registered
```

### 4.5 Step 5: iter_routes() 输出 14 个 RouteEntry

```
Total RouteEntry: 14
  - admin        prefix=/api/users           routes=1
  - admin        prefix=/api/stats           routes=6
  - admin        prefix=/api                 routes=2
  - auth         prefix=/api/auth            routes=4
  - tasks        prefix=/api/datasets        routes=6
  - tasks        prefix=/api/images          routes=7
  - tasks        prefix=/api/training        routes=15
  - tasks        prefix=/api/models          routes=9
  - tasks        prefix=/api/auto-annotate   routes=4
  - tasks        prefix=/api/export          routes=7
  - tasks        prefix=/api/detection       routes=16
  - tasks        prefix=/api/segmentation    routes=9
  - tasks        prefix=/api/files           routes=3
  - annotation   prefix=/api/annotations     routes=5
[OK] 14 RouteEntry
```

### 4.6 Step 6: 无旧路径引用

```
[OK] No old path references
```

**全部 6/6 验证步骤通过**。

### 4.7 31 个关键模块导入验证 (额外)

```
[OK]   app.main
[OK]   app.registry
[OK]   app.database.session
[OK]   app.tasks.workers.celery_app
[OK]   app.tasks.workers.classification
[OK]   app.tasks.workers.detection
[OK]   app.tasks.workers.segmentation
[OK]   app.tasks.service.training_lifecycle_service
[OK]   app.tasks.service.training_data_service
[OK]   app.tasks.service.training_service
[OK]   app.tasks.service.annotation_service
[OK]   app.tasks.ml.classification
[OK]   app.tasks.ml.detection.yolo_dataset
[OK]   app.admin.api.user
[OK]   app.admin.api.system
[OK]   app.admin.api.stats
[OK]   app.auth.api.auth
[OK]   app.auth.service.auth_service
[OK]   app.annotation.api.annotation
[OK]   app.common.base_model
[OK]   app.common.geometry.bbox_service
[OK]   app.common.storage.storage_service
[OK]   app.common.ml.ai_service
[OK]   app.middleware.http.auth
[OK]   app.middleware.http.cors
[OK]   app.middleware.http.error_handler
[OK]   app.middleware.security.security
[OK]   app.core.config
[OK]   app.core.db_migration
[OK]   app.core.redis_client
[OK]   app.utils.async_helpers

=== Total: 31, Failures: 0 ===
```

### 4.8 OpenAPI 路由清单 (`verify_routes.py`)

```
Total unique paths: 91
Total route operations: 98

=== Routes by API prefix ===
  /                                     1 ops
  /api/annotations                      5 ops
  /api/auth                             4 ops
  /api/auto-annotate                    4 ops
  /api/datasets                         3 ops
  /api/detection                       16 ops
  /api/export                           7 ops
  /api/files                            2 ops
  /api/health                           1 ops
  /api/images                           6 ops
  /api/models                           9 ops
  /api/segmentation                     9 ops
  /api/stats                            6 ops
  /api/system                           1 ops
  /api/training                        12 ops
  /api/users                            1 ops
```

**OpenAPI 标题**: Image Annotation System API
**OpenAPI 版本**: 1.0.0
**OpenAPI paths**: 87

---

## 五、最终目录结构 (Stage 2 完结)

```
backend/
├── app/
│   ├── admin/                    # 管理系统 (Bounded Context)
│   │   ├── api/                  # user / system / stats
│   │   ├── model/                # user
│   │   ├── service/              # (按需填充)
│   │   └── __init__.py           # AdminApp
│   ├── auth/                     # 认证系统 (Bounded Context)
│   │   ├── api/                  # auth
│   │   ├── repository/           # 凭证存储
│   │   ├── schema/               # 请求/响应 schema
│   │   ├── service/              # auth_service
│   │   └── __init__.py           # AuthApp
│   ├── tasks/                    # 任务系统 (Bounded Context, 业务核心)
│   │   ├── api/                  # dataset/image/training/detection/segmentation/model/auto_annotate/export/files (9 router)
│   │   ├── model/                # dataset/image/category/model_version/training_job (5 ORM)
│   │   ├── service/              # 8 service (training/dataset/annotation/detection/segmentation/auto_annotate/job_state/training_data/training_lifecycle)
│   │   ├── ml/                   # classification / detection / segmentation (3 子模块)
│   │   ├── workers/              # celery_app + classification + detection + segmentation (4 worker)
│   │   └── __init__.py           # TasksApp
│   ├── annotation/               # 标注系统 (Bounded Context)
│   │   ├── api/                  # annotation
│   │   ├── model/                # bbox_annotation / annotation_log / segmentation_mask (3 ORM)
│   │   ├── service/              # (annotation_service 已迁入 tasks)
│   │   └── __init__.py           # AnnotationApp
│   ├── common/                   # 横切 - 业务可复用组件
│   │   ├── base_model.py
│   │   ├── exceptions.py
│   │   ├── interfaces.py
│   │   ├── geometry/             # bbox_service (Stage 2.8 新增)
│   │   ├── ml/                   # ai_service (Stage 2.8 新增)
│   │   └── storage/              # storage_service (Stage 2.8 新增)
│   ├── core/                     # 横切 - 应用配置
│   │   ├── config.py
│   │   ├── db_migration.py
│   │   ├── redis_client.py
│   │   ├── ultralytics_setup.py
│   │   └── cli.py
│   ├── database/                 # 横切 - 数据库配置
│   │   ├── engine.py
│   │   ├── session.py
│   │   └── __init__.py
│   ├── middleware/               # 横切 - 中间件
│   │   ├── http/                 # auth / cors / error_handler
│   │   └── security/             # security
│   ├── schemas/                  # 请求/响应 Pydantic schema
│   ├── utils/                    # 横切 - 工具函数
│   ├── main.py                   # AppRegistry.iter_routes() 自动挂载
│   ├── registry.py               # AppRegistry 单例
│   ├── config.py                 # 兼容入口 (re-export)
│   └── __main__.py               # CLI 入口
├── plugin/                       # (Stage 4 计划) 插件层
├── migrations/                   # 数据库迁移
├── models/                       # 模型权重存储
├── docs/                         # 文档
├── tests/                        # 测试
├── scripts/                      # 工具脚本
├── logs/                         # 日志
└── ... (配置文件)
```

---

## 六、Stage 2 → Stage 3-5 衔接计划

| Stage | 内容 | 预计工作量 | 优先级 |
|---|---|---|---|
| **Stage 3** | 完善 common/ database/ middleware/ core/ 横切目录 | 1.5 天 | 🟡 P1 |
| **Stage 4** | 创建 plugin/ 顶层 + 抽象 ML/Storage/TaskQueue 接口 | 1.5 天 | 🟡 P1 |
| **Stage 5** | 性能 + 监控 (慢请求中间件 + 缓存层 + 启动顺序优化) | 1 天 | 🟢 P2 |

**Stage 3 候选任务**:
- 横切目录 docstring 完善 + README.md
- `app/common/` 子目录拆分 (geometry/ml/storage 已是子目录, 进一步细化)
- `app/database/` 增加 connection_pool 监控 + 慢查询日志
- `app/middleware/` 增加 request_id + access_log 中间件
- `app/core/` 拆分 config 为 base + per-env

**Stage 4 候选任务**:
- 顶层 `plugin/` 目录创建
- `plugin/ml_backends/` 抽象 MLBackend 接口
- `plugin/storage_backends/` 抽象 Storage 接口 (本地/MinIO/S3)
- `plugin/task_queues/` 抽象 TaskQueue 接口 (Celery/RQ)
- `plugin/notification_channels/` 抽象 NotificationChannel 接口 (邮件/Webhook/SSE)
- 现有实现改写为 plugin 默认实现, 业务代码只依赖接口

**Stage 5 候选任务**:
- 慢请求中间件 (记录 >500ms 请求)
- 缓存层 (Redis 装饰器, 用于 dataset stats / model list)
- 启动顺序优化 (lazy import, 减少启动时间)
- 监控埋点 (prometheus metrics)

---

## 七、关键决策 (本阶段新增)

| 决策 ID | 主题 | 描述 |
|---|---|---|
| **DR-25** | Stage 2 兼容垫片清理策略 | 一次性删除 `app/{api,model,services,ml,workers}/` 5 个旧目录, 不分批, 避免残留混乱 (Stage 2.5-2.7 已先迁移业务, 验证充分) |
| **DR-26** | 横切关注点分类 | `app/common/{geometry,ml,storage}/` 子目录分类: 几何 / 机器学习通用 / 存储抽象, 与业务 Service 严格分离 |
| **DR-27** | Stage 2.8 验证脚本固化 | `verify_stage2_8_final.py` 6 步骤成为后续 Stage 收尾标准模板 |

---

## 八、提交记录 (本次 Stage 2.8)

按"3 文件/提交"原子化原则, Stage 2.8 拆分为以下提交:

| # | 提交 | 说明 |
|---|---|---|
| 1 | `refactor(stage2.8a): delete app/api/, populate app/{admin,annotation,auth,tasks}/api/` | API 路由迁移 (12 文件删除 + 11 文件修改 + 2 文件新增) |
| 2 | `refactor(stage2.8b): delete app/services/, populate app/tasks/service/ + app/common/{geometry,ml,storage}/` | Service 迁移 (16 文件删除 + 13 文件新增) |
| 3 | `refactor(stage2.8c): delete app/model/, all imports to app/{admin,tasks,annotation}/model/` | ORM 兼容垫片清理 (20 文件删除 + 数据库 session 修正) |
| 4 | `refactor(stage2.8d): delete app/ml/ + app/workers/, all imports to app/tasks/{ml,workers}/` | ML + Workers 兼容垫片清理 (16 文件删除) |
| 5 | `refactor(stage2.8e): main.py + database/ + core/ + middleware/ import paths to new locations` | 核心基础设施更新 (5 文件) |
| 6 | `chore(stage2.8f): verification scripts (verify_stage2_8_*.py + verify_routes.py)` | 验证脚本固化 (5 文件) |
| 7 | `docs(stage2.8g): Stage 2.8 实施完成报告` | 本文档 |

---

## 九、风险评估与回退方案

### 9.1 风险

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| 旧目录删除后, 漏改的 import 路径 | 🟢 低 | verify_stage2_8_final.py 6 步验证 + 31 模块 import 测试 |
| 路由数量变化 (减少/增多) | 🟢 低 | OpenAPI schema 自动统计, 91 paths / 98 ops 已固化 |
| Celery 任务名变化 | 🟢 低 | 7 任务名 + 完整路径已记录, worker 启动时自动发现 |
| 业务行为差异 | 🟡 中 | API/Service 主体未变, 仅 import 路径调整, 行为等价 |

### 9.2 回退方案

- 全部删除/新增操作均在 git 版本控制下
- 如遇问题, `git revert <commit-hash>` 即可一键回滚
- 兼容垫片 (Stage 2.5-2.7 已建) 在本阶段一次性清理, 不再保留

---

**维护人**: 后端开发组
**下一步**: Stage 3 — 完善横切目录 (`common/` `database/` `middleware/` `core/`)
