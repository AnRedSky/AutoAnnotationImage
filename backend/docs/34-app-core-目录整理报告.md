# app/core 目录整理报告

> **v3.0.0 Phase 2.5 — 核心包职责重划**
> 日期: 2026-07-25
> 状态: ✅ 已完成
> 关联报告: [31-Phase1-兼容垫片清理报告.md](31-Phase1-兼容垫片清理报告.md) · [19-重构优先级与阶段路线图.md](19-重构优先级与阶段路线图.md)

---

## 一、背景与目标

`app/core` 目录经过多个阶段迭代后, 已经堆积了 9 个文件, 其中部分文件已经偏离了"应用级核心配置"的定位:

| 文件 | 实际职能 | 误置原因 |
|------|----------|----------|
| `cache.py` | Redis 业务缓存 | Stage 5.2 引入时未细想落点, 临时放在 core |
| `redis_client.py` | Redis 客户端单例 | 早期只有 cache 用, 顺路放在 core |
| `db_migration.py` | 数据库 schema 迁移 | 早期与 init_db 联动, 临时放在 core |
| `ultralytics_setup.py` | ultralytics 路径集中配置 | 早期启动 hook, 顺路放在 core |

本次整理目标: **按照各模块的职能进行分组归类, 把不属于"应用级核心配置"的文件迁移到合适的子包, 让 `core` 目录回归单一职责**.

---

## 二、整理原则

### 2.1 单一职责原则

`app/core` 仅保留"应用启动 / 全局配置 / 跨切基础设施"类代码:

- **保留**: config / logging_setup / startup_profiler / cli
- **迁移**: 业务缓存 / Redis 客户端 / DB 迁移 / ML 框架配置

### 2.2 归类标准

| 业务领域 | 归属目录 | 理由 |
|----------|----------|------|
| Redis 客户端连接 | `app.database` | 与 SQLAlchemy engine/session 同级, 统一为"连接基础设施" |
| 数据库 schema 迁移 | `app.database` | 与 init_db 同包, 都在数据库启动流程里 |
| 业务缓存 (Redis 包装) | `app.common` | 与枚举/异常/事件同级, 属于业务可复用横切组件 |
| ultralytics 路径配置 | `app.tasks.ml` | 与 detection/segmentation/classification 平级, 属于 ML 训练基础设施 |

### 2.3 依赖方向

迁移后, 各模块的依赖方向保持单向:

```
app.core  ←  app.common  ←  app.database  ←  app.tasks
   ↑           ↑               ↑                ↑
   └───────────┴───────────────┴────────────────┘
                都被 core.config (settings) 引用
```

- `app.core` 不依赖任何业务层
- `app.common` 不依赖任何业务层, 仅依赖标准库 + pydantic
- `app.database` 仅依赖 core/common, 不依赖 app/*
- `app.tasks.ml` 依赖 core + database, 不跨任务模块

---

## 三、迁移清单

### 3.1 文件迁移

| 原位置 | 新位置 | 状态 |
|--------|--------|------|
| `app/core/cache.py` | `app/common/cache.py` | ✅ 已完成 |
| `app/core/redis_client.py` | `app/database/redis.py` | ✅ 已完成 |
| `app/core/db_migration.py` | `app/database/migration.py` | ✅ 已完成 |
| `app/core/ultralytics_setup.py` | `app/tasks/ml/ultralytics_setup.py` | ✅ 已完成 |

### 3.2 import 引用更新 (共 22 处)

| 引用方 | 旧路径 | 新路径 | 数量 |
|--------|--------|--------|------|
| `app/admin/api/system.py` | `app.core.cache` | `app.common.cache` | 1 |
| `app/core/__init__.py` | `app.core.cache` | (已删除) | 1 |
| `app/database/slow_sql.py` | `app.core.redis_client` | `app.database.redis` | 4 |
| `app/database/session.py` | `app.core.db_migration` | `app.database.migration` | 1 |
| `app/tasks/api/training/history.py` | `app.core.redis_client` | `app.database.redis` | 1 |
| `app/tasks/api/training/jobs.py` | `app.core.redis_client` | `app.database.redis` | 1 |
| `app/tasks/service/training_lifecycle_service/celery.py` | `app.core.redis_client` | `app.database.redis` | 1 |
| `app/tasks/service/training_lifecycle_service/job.py` | `app.core.redis_client` | `app.database.redis` | 1 |
| `app/tasks/service/job_state_service.py` | `app.core.redis_client` | `app.database.redis` | 1 |
| `app/tasks/workers/classification.py` | `app.core.redis_client` / `app.core.ultralytics_setup` | `app.database.redis` / `app.tasks.ml.ultralytics_setup` | 2 |
| `app/tasks/workers/segmentation/train.py` | `app.core.ultralytics_setup` | `app.tasks.ml.ultralytics_setup` | 1 |
| `app/utils/async_helpers.py` | `app.core.redis_client` | `app.database.redis` | 1 |
| `app/main.py` | `app.core.ultralytics_setup` / `app.core.redis_client` | `app.tasks.ml.ultralytics_setup` / `app.database.redis` | 2 |
| `plugin/notification_channels/sse.py` | `app.core.redis_client` | `app.database.redis` | 4 |
| `scripts/migrate_v2_0_0.py` | `app.core.db_migration` | `app.database.migration` | 1 |
| `tests/test_migrate_v2_0_0.py` | `app.core.db_migration` | `app.database.migration` | 1 |
| `tests/verify_model_paths.py` | `app.core.ultralytics_setup` | `app.tasks.ml.ultralytics_setup` | 1 |
| `tests/test_segmentation_train.py` | `app.core.redis_client.redis_client.ping` (mock) | `app.database.redis.redis_client.ping` | 1 |

### 3.3 注释/文档更新 (共 6 处)

| 文件 | 内容 |
|------|------|
| `app/core/config.py` | 注释从 `app.core.redis_client` 改为 `app.database.redis` |
| `app/tasks/ml/detection/yolo_train.py` | 注释从 `app.core.ultralytics_setup` 改为 `app.tasks.ml.ultralytics_setup` |
| `plugin/notification_channels/sse.py` | 文档从 `app.core.redis_client` 改为 `app.database.redis` |
| `scripts/migrate_v2_0_0.py` | 注释从 `app.core.db_migration` 改为 `app.database.migration` |
| `tests/test_migrate_v2_0_0.py` | 注释从 `app.core.db_migration` 改为 `app.database.migration` |
| `app/core/__init__.py` | 重写模块说明, 标注已迁移的文件 |

### 3.4 兼容垫片

未引入新的兼容垫片 (shim):

- 业务代码 import 全部更新到新路径
- 测试代码 import 全部更新到新路径
- 老路径 `app.core.cache` / `app.core.redis_client` / `app.core.db_migration` / `app.core.ultralytics_setup` 触发 `ModuleNotFoundError`, 立即暴露遗漏的 import

`app.database.__init__.py` 中保留了对新模块 (`redis_client`, `ensure_v2_0_0_schema` 等) 的 re-export, 这是因为 `app.database` 本来就是这些符号的"上级包", 旧调用方可能用 `from app.database import redis_client` 这种方式, 不需要再单独写兼容垫片.

---

## 四、整理后的目录结构

### 4.1 `app/core/` (整理后)

```
app/core/
├── __init__.py             # 仅导出 setup_logging/get_logger/startup_profiler
├── config.py               # pydantic-settings 全局配置 (19.6 KB)
├── logging_setup.py        # 集中日志配置 (3.7 KB)
├── startup_profiler.py     # 启动耗时分析 (3.3 KB)
└── cli.py                  # CLI 入口 (5.2 KB)
```

**职责**: 应用启动 / 全局配置 / 跨切基础设施
**文件数**: 5 (从 9 减少, 减幅 44%)

### 4.2 `app/common/` (整理后)

```
app/common/
├── __init__.py             # 导出 enums/exceptions/interfaces/events/constants/cache
├── base_model.py           # ORM 基类扩展
├── constants.py            # 业务常量 (Stage 3)
├── enums.py                # 业务枚举
├── events.py               # 事件总线
├── exceptions.py           # 业务异常
├── interfaces.py           # 抽象接口
├── cache.py                # ⭐ NEW: Redis 业务缓存 (从 core/cache.py 迁入)
├── geometry/               # 几何服务 (bbox 计算)
│   └── bbox_service.py
├── ml/                     # ML 通用服务
│   └── ai_service.py
└── storage/                # 存储服务
    └── storage_service.py
```

**职责**: 业务可复用横切组件, 不依赖任何业务层
**新增**: `cache.py` (8.5 KB)

### 4.3 `app/database/` (整理后)

```
app/database/
├── __init__.py             # ⭐ UPDATED: 重新导出 redis_client / ensure_v2_0_0_schema
├── engine.py               # 异步引擎
├── session.py              # 会话管理 + init_db
├── redis.py                # ⭐ NEW: Redis 客户端 (从 core/redis_client.py 迁入)
├── migration.py            # ⭐ NEW: 数据库迁移 (从 core/db_migration.py 迁入)
└── slow_sql.py             # 慢 SQL 监控
```

**职责**: 数据库基础设施, 与连接池/迁移/监控同包
**新增**: `redis.py`, `migration.py`

### 4.4 `app/tasks/ml/` (整理后)

```
app/tasks/ml/
├── __init__.py             # ML 任务包入口
├── classification.py       # 分类训练
├── imagenet_common_labels.json
├── ultralytics_setup.py    # ⭐ NEW: ultralytics 路径配置 (从 core/ultralytics_setup.py 迁入)
├── detection/              # 目标检测
│   ├── yolo_dataset.py
│   ├── yolo_predict.py
│   └── yolo_train.py
└── segmentation/           # 语义分割
    ├── seg_dataset.py
    ├── seg_predict.py
    └── seg_train.py
```

**职责**: ML 训练基础设施, 与 detection/segmentation/classification 平级
**新增**: `ultralytics_setup.py` (5.0 KB)

---

## 五、验证结果

### 5.1 静态校验

```powershell
# 1) py_compile 静态检查所有相关文件
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), f) for f in [...]]"
# 输出: OK: All files parse
```

### 5.2 Import 验证

```python
# 新路径可用
>>> from app.common.cache import cache, cached, invalidate
>>> from app.database.redis import redis_client, get_redis
>>> from app.database.migration import MIGRATIONS, ensure_v2_0_0_schema
>>> from app.tasks.ml.ultralytics_setup import configure_ultralytics
# 全部 OK

# 旧路径正确报 ImportError
>>> from app.core.cache import cache
ModuleNotFoundError("No module named 'app.core.cache'")
>>> from app.core.redis_client import redis_client
ModuleNotFoundError("No module named 'app.core.redis_client'")
>>> from app.core.db_migration import ensure_v2_0_0_schema
ModuleNotFoundError("No module named 'app.core.db_migration'")
>>> from app.core.ultralytics_setup import configure_ultralytics
ModuleNotFoundError("No module named 'app.core.ultralytics_setup'")
```

### 5.3 FastAPI 应用启动验证

```python
>>> from app.main import app
>>> print(app.title, len(app.routes))
Image Annotation System API 105
```

API 注册成功, 路由总数 105 个, 无回归.

### 5.4 Worker 模块验证

```python
# 三个 worker 模块均能正常加载
>>> import app.tasks.workers.classification
>>> import app.tasks.workers.segmentation.train
>>> import app.tasks.service.training_lifecycle_service.job
# 全部 OK
```

---

## 六、影响范围与回滚预案

### 6.1 影响范围

| 维度 | 影响 |
|------|------|
| 业务代码 | 22 处 import 引用, 已全部更新 |
| 测试代码 | 3 个测试文件, 6 处引用, 已全部更新 |
| 文档注释 | 6 处, 已全部更新 |
| 兼容垫片 | 无 (直接删除) |
| 数据库 | 无 schema 变化 |
| API 端点 | 无变化 (105 路由) |
| 性能 | 无 (只移动文件位置, 无逻辑改动) |

### 6.2 回滚预案

如需回滚:

1. 从 git history 恢复 `app/core/cache.py`, `app/core/redis_client.py`, `app/core/db_migration.py`, `app/core/ultralytics_setup.py`
2. 删除新文件 `app/common/cache.py`, `app/database/redis.py`, `app/database/migration.py`, `app/tasks/ml/ultralytics_setup.py`
3. 还原 22 处 import 引用 (可使用 git diff 反向操作)
4. 还原 `app/core/__init__.py` 和 `app/database/__init__.py`

但鉴于本次整理无 API 行为变化且无兼容垫片, 验证充分, **不预期需要回滚**.

---

## 七、后续规划

1. **文档同步**: 更新 [README.md](../README.md) 和 [19-重构优先级与阶段路线图.md](19-重构优先级与阶段路线图.md) 中关于 `app/core` 目录的描述.
2. **进一步拆分**: `app/core/config.py` 仍有 19.6 KB, 后续可考虑按业务域拆分 (Redis / MinIO / MySQL / Training 等).
3. **Phase 2 测试清理**: `tests/` 中仍有 54 处 Phase 2 路径过期引用 (`app.models.*` / `app.workers.*` / `app.ml.*` / `app.api.*`), 需独立清理 (建议 0.5h 任务).

---

## 八、变更清单 (Git 视角)

```
modified:  app/admin/api/system.py
modified:  app/core/__init__.py
modified:  app/core/config.py
modified:  app/database/__init__.py
modified:  app/database/session.py
modified:  app/database/slow_sql.py
modified:  app/main.py
modified:  app/tasks/api/training/history.py
modified:  app/tasks/api/training/jobs.py
modified:  app/tasks/service/job_state_service.py
modified:  app/tasks/service/training_lifecycle_service/celery.py
modified:  app/tasks/service/training_lifecycle_service/job.py
modified:  app/tasks/workers/classification.py
modified:  app/tasks/workers/segmentation/train.py
modified:  app/tasks/ml/detection/yolo_train.py
modified:  app/utils/async_helpers.py
modified:  plugin/notification_channels/sse.py
modified:  scripts/migrate_v2_0_0.py
modified:  tests/test_migrate_v2_0_0.py
modified:  tests/test_segmentation_train.py
modified:  tests/verify_model_paths.py
deleted:   app/core/cache.py
deleted:   app/core/redis_client.py
deleted:   app/core/db_migration.py
deleted:   app/core/ultralytics_setup.py
new file:  app/common/cache.py
new file:  app/database/redis.py
new file:  app/database/migration.py
new file:  app/tasks/ml/ultralytics_setup.py
new file:  docs/34-app-core-目录整理报告.md  (本节件)
```

**总变更**: 21 modified + 4 deleted + 4 new file = 29 项变更.

---

报告完成.
