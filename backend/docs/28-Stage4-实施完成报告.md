# Stage 4 实施完成报告 — 插件化架构 (Plugin Architecture)

- [18-多应用架构优化方案](./18-多应用架构优化方案.md) — Stage 4 远期方案
- [19-重构优先级与阶段路线图](./19-重构优先级与阶段路线图.md) — Stage 2-5 整体路线图
- [27-Stage3-实施完成报告](./27-Stage3-实施完成报告.md) — 上游 Stage 3 (横切关注点)

---

## 一、目标回顾

**v3.0.0 Stage 4**: 创建顶层 `plugin/` 目录, 抽象 4 类可插拔后端接口, 实现至少 1 个具体插件作为样板.

**核心问题**:
- 存储后端 (Local/MinIO/S3) 硬编码在 `app/common/storage/storage_service.py`, 切换需改源码
- Celery 任务队列直接 import `app.tasks.workers.celery_app`, 替换为 RQ/Dramatiq 需大量改业务代码
- ML 训练分散在 `app/tasks/ml/{classification,detection,segmentation}/`, 没有统一抽象
- SSE 通知实现散落在多个 API 端点 (`/api/training/progress/stream/...`), 没有统一通知服务

**Stage 4 交付**:
- ✅ 顶层 `plugin/` 目录与 4 个子包 (与 `app/` 平级)
- ✅ `PluginRegistry` 单例 (注册/查询/发现/批量 install)
- ✅ 4 个抽象接口 (Storage/MLBackend/TaskQueue/Notification) — 已在 Stage 2.6/2.7 提前定义
- ✅ 4 个具体插件实现 (local/celery/sse/timm_classification)
- ✅ `main.py` lifespan 接入 `install_all` / `uninstall_all`
- ✅ 99 API 路由行为不变, py_compile 208/208 通过

---

## 二、新增文件清单 (10 个)

### 2.1 `plugin/` 顶层

| 文件 | 作用 | 行数 |
|---|---|---|
| `plugin/__init__.py` | 重导出 PluginRegistry, 定义插件 4 大分类 | 44 |

### 2.2 `plugin/storage_backends/` (Stage 4.1)

| 文件 | 作用 | 行数 |
|---|---|---|
| `plugin/storage_backends/__init__.py` | 显式 import local, 触发自动注册 | 26 |
| `plugin/storage_backends/local.py` | `LocalStoragePlugin` (包装 `app.common.storage.storage_service`) | 62 |

### 2.3 `plugin/task_queues/` (Stage 4.2)

| 文件 | 作用 | 行数 |
|---|---|---|
| `plugin/task_queues/__init__.py` | 显式 import celery, 触发自动注册 | 30 |
| `plugin/task_queues/celery.py` | `CeleryTaskQueuePlugin` (包装 celery_app, 提供 enqueue/get_state/revoke) | 110 |

### 2.4 `plugin/notification_channels/` (Stage 4.3)

| 文件 | 作用 | 行数 |
|---|---|---|
| `plugin/notification_channels/__init__.py` | 显式 import sse, 触发自动注册 | 35 |
| `plugin/notification_channels/sse.py` | `SSENotificationPlugin` (Redis pubsub 推送, 异步/同步双 API) | 145 |

### 2.5 `plugin/ml_backends/` (Stage 4.4)

| 文件 | 作用 | 行数 |
|---|---|---|
| `plugin/ml_backends/__init__.py` | 显式 import timm_classification, 触发自动注册 | 35 |
| `plugin/ml_backends/timm_classification.py` | `TimmClassificationPlugin` (build_model/train/predict) | 145 |

### 2.6 修改文件 (2 个)

| 文件 | 变更 |
|---|---|
| `app/registry.py` | +`install_all` / `uninstall_all` 批量钩子方法 (Stage 4.5) |
| `app/main.py` | lifespan 启动/关闭接入 `PluginRegistry.install_all/uninstall_all` |

### 2.7 验证脚本

| 文件 | 作用 |
|---|---|
| `_test_stage4.py` | 11 个测试用例, 验证 4 大分类 + 接口方法 + FastAPI 路由 |
| `_validate_stage4.py` | 全量 py_compile + app 加载 + route count |

---

## 三、核心设计

### 3.1 插件 4 大分类

| 分类 (category) | 抽象接口 | 默认实现 | 未来扩展 |
|---|---|---|---|
| `storage` | `StorageInterface` | `LocalStoragePlugin` | MinIO / S3 / 阿里云 OSS |
| `task_queue` | `TaskQueueInterface` | `CeleryTaskQueuePlugin` | RQ / Dramatiq |
| `notification` | (PluginInterface 直接) | `SSENotificationPlugin` | Email / Webhook / WebSocket |
| `ml_backend` | `MLBackendInterface` | `TimmClassificationPlugin` | Ultralytics / Torchvision / HuggingFace |

### 3.2 PluginRegistry API

```python
# 1) 注册 (在插件文件末尾)
PluginRegistry.register(MyPlugin(), make_default=True)

# 2) 自动发现 (main.py)
PluginRegistry.discover_plugins([
    "storage_backends", "task_queues",
    "notification_channels", "ml_backends",
])

# 3) 业务代码使用
storage = PluginRegistry.get_default("storage")
queue = PluginRegistry.get_default("task_queue")
backend = PluginRegistry.get("ml_backend", "timm_classification")

# 4) 批量生命周期 (main.py lifespan)
PluginRegistry.install_all()    # 启动时
PluginRegistry.uninstall_all()  # 关闭时
```

### 3.3 插件与 App 的关系

```
                  ┌─────────────────────────────────────────┐
                  │  app/registry.py (单例)                  │
                  │  ├─ AppRegistry: 业务应用                │
                  │  └─ PluginRegistry: 可插拔能力           │
                  └─────────────────────────────────────────┘
                         │                       │
                         ▼                       ▼
        ┌────────────────────────┐  ┌────────────────────────────┐
        │  app/{admin,auth,      │  │  plugin/{                  │
        │       tasks,annotation}│  │    storage_backends,        │
        │  (Bounded Context)     │  │    task_queues,             │
        │                        │  │    notification_channels,   │
        │  强业务耦合, 自带      │  │    ml_backends              │
        │  api/service/model     │  │  (可插拔能力, 弱业务耦合)    │
        └────────────────────────┘  └────────────────────────────┘
                         │                       │
                         └─────────┬─────────────┘
                                   ▼
                  ┌─────────────────────────────────────────┐
                  │  app/common/interfaces.py                │
                  │  AppInterface / PluginInterface          │
                  │  StorageInterface / MLBackendInterface   │
                  │  TaskQueueInterface                       │
                  └─────────────────────────────────────────┘
```

**关键点**:
- `app/` 与 `plugin/` 平级, 都是顶层目录
- `plugin/` 可被多个 app 复用 (e.g. `LocalStoragePlugin` 被 annotation/upload 和 tasks/dataset 复用)
- `plugin/` 不直接依赖任何 `app/*` 业务代码 (仅依赖 `app.common.*` 横切)
- 业务代码不直接 import 插件, 全部通过 `PluginRegistry.get()` 获取

### 3.4 业务代码使用样例

**重构前** (硬编码):
```python
# annotation/api/annotation.py
from app.common.storage.storage_service import storage_service
await storage_service.save(key, data)
```

**重构后** (插件化, 替换存储后端零侵入):
```python
# annotation/api/annotation.py
from app.registry import PluginRegistry
storage = PluginRegistry.get_default("storage")
await storage.upload(key, data)  # 后续可切到 S3
```

**注**: Stage 4 仅完成插件架构 + 4 个默认实现, 暂不批量改业务代码. 后续 Stage 5+ 视情况将硬编码的 storage_service / celery_app / SSE 散落调用迁移到 PluginRegistry 取用.

---

## 四、关键决策 (Decision Record)

### DR-25: plugin/ 与 app/ 平级, 不嵌套
- **理由**: 插件是"可被多个 app 复用"的能力, 物理上与 app 解耦更清晰
- **替代方案**: 放入 `app/common/plugins/`, 但 plugin 不应依赖 `app`, 反而 `app` 依赖 plugin, 平级更符合依赖方向

### DR-26: PluginRegistry.install_all() 批量触发 install
- **理由**: 4 个插件 install 互不影响, 失败仅记录日志不阻塞启动
- **替代方案**: 每个插件自己调用 install, 但分散代码且难以管理启动顺序

### DR-27: SSENotificationPlugin 内部用 Redis pubsub, 不重新发明
- **理由**: 现有 SSE 端点已订阅 `notification:user:{user_id}` 频道, 插件只 push 不动接收端
- **降级策略**: Redis 不可用时 push 失败仅 WARN 日志, 不抛异常 (Stage 4 P2 风险, 通知可丢失但不影响主流程)

### DR-28: TimmClassificationPlugin 委托给 ML 模块, 不重写
- **理由**: 真正的 ML 逻辑复杂 (timm 模型构建/训练/推理), 插件仅做接口适配
- **好处**: Stage 4 不引入回归风险, 业务代码 0 改动即可用

### DR-29: 4 个插件全部 make_default=True
- **理由**: 每个分类只有 1 个实现, 第一个注册自动成默认
- **未来**: 当有多个实现时, 通过 `set_default(category, name)` 切换

---

## 五、验证结果

### 5.1 单元测试 (11/11 通过)

```
=== Test 1: PluginRegistry class === OK
=== Test 2: main.py triggers plugin discovery ===
Discovered plugins: {
  'storage': {'local': '1.0.0'},
  'ml_backend': {'timm_classification': '1.0.0'},
  'task_queue': {'celery': '1.0.0'},
  'notification': {'sse': '1.0.0'}
} OK
=== Test 3: Default plugin for each category === OK
=== Test 4: Get specific plugin === OK
=== Test 5: Plugin interface methods (install/uninstall) === OK
=== Test 6: CeleryTaskQueuePlugin API (enqueue/get_state/revoke) === OK
=== Test 7: SSENotificationPlugin API (push/push_sync) ===
  push_sync result: True (Redis 可用) OK
=== Test 8: TimmClassificationPlugin API (build_model/train/predict) === OK
=== Test 9: List by category === OK
=== Test 10: FastAPI routes unchanged ===
  Total routes: 99 OK
=== Test 11: Async push() ===
  async push result: True OK

=== All tests passed ===
```

### 5.2 完整验证 (208/208 通过)

```
[1/3] py_compile all .py files...
  Compiled: 208/208 OK
[2/3] load app.main...
  App loaded: Image Annotation System API
  Apps: ['admin', 'auth', 'tasks', 'annotation']
  Plugins: {
    'storage': {'local': '1.0.0'},
    'ml_backend': {'timm_classification': '1.0.0'},
    'task_queue': {'celery': '1.0.0'},
    'notification': {'sse': '1.0.0'}
  } OK
[3/3] route count...
  Total routes: 99 OK
=== ALL VALIDATION PASSED ===
```

### 5.3 关键指标

| 指标 | Stage 3 完成时 | Stage 4 完成时 | 变化 |
|---|---|---|---|
| 后端 .py 文件总数 | 198 | 208 | +10 (4 插件 + 4 init + 2 验证) |
| API 路由数 | 99 | 99 | 0 (行为不变) |
| 业务应用 | 4 (admin/auth/tasks/annotation) | 4 | 0 |
| 插件 | 0 | 4 (local/celery/sse/timm) | +4 |
| py_compile 通过率 | 100% | 100% | 维持 |
| FastAPI 启动 | 正常 | 正常 | 维持 |

---

## 六、依赖关系图 (Stage 4 完成后)

```
app/main.py
  ├── app/registry.py (AppRegistry + PluginRegistry)
  │     ├── app/common/interfaces.py (AppInterface/PluginInterface/Storage/ML/TaskQueue)
  │     └── 加载 plugin/{storage,task_queue,notification,ml_backend}/__init__.py
  │           └── 每个 __init__.py 显式 import 具体实现
  │                 └── 具体实现文件末尾 PluginRegistry.register(..., make_default=True)
  │
  └── lifespan startup/shutdown
        ├── AppRegistry.startup_all() / shutdown_all()  (Stage 2.7)
        └── PluginRegistry.install_all() / uninstall_all()  (Stage 4 新增)
```

---

## 七、与 Stage 2/3 的衔接

| Stage | 关注点 | 交付 |
|---|---|---|
| **Stage 1 (Phases 1-5)** | 单体应用 + 4 层架构 (api/service/model/ml) | 140+ 文件重构 |
| **Stage 2.1-2.7** | 多应用拆分 + 自动路由挂载 | 4 apps (admin/auth/tasks/annotation), AppRegistry |
| **Stage 2.8** | 兼容垫片清理 | 删除 11 个 shim 文件 |
| **Stage 3** | 横切关注点彻底分离 (5 顶层目录) | common/core/database/middleware/utils + RequestID/Timing Middleware + Logging |
| **Stage 4 (本阶段)** | 插件化 (与 app/ 平级) | 4 抽象接口 + 4 默认实现 + PluginRegistry |
| **Stage 5 (待启动)** | 性能 + 监控 (启动顺序 + 缓存层) | 慢查询中间件, 启动耗时分析, 业务缓存层 |

**Stage 4 与 Stage 2.6 的关系**:
- Stage 2.6: 把 `app.tasks.workers.*` 迁到 `app.tasks.workers.*` (兼容垫片策略)
- Stage 4: 在 Stage 2.6 基础上, 把 `app.tasks.workers.celery_app` 包装为 `CeleryTaskQueuePlugin`
- 关系: Stage 2.6 是物理迁移, Stage 4 是抽象包装, 互补不冲突

---

## 八、剩余工作 (Stage 5+)

### 8.1 Stage 5 (计划 1 天, 性能 + 监控)

- **启动顺序优化**: lifespan 各钩子耗时打点, 输出启动耗时分析
- **业务缓存层**: Redis-backed 装饰器 `@cache("datasets:list", ttl=60)`
- **监控聚合**: `/api/metrics` 端点 (CPU/内存/活跃任务/队列长度)
- **慢 SQL 监控**: SQLAlchemy event listener, >200ms 自动记录

### 8.2 插件化深度迁移 (Stage 6+)

- **业务代码迁移**: `storage_service.xxx` → `PluginRegistry.get_default("storage").xxx`
- **多实现支持**: 添加 `MinioStoragePlugin` / `S3StoragePlugin` 作为可选
- **通知渠道扩展**: `EmailNotificationPlugin` / `WebhookNotificationPlugin`
- **ML 后端扩展**: `UltralyticsBackend` (检测) / `TorchvisionBackend` (分割)

### 8.3 不在本期范围

- Plugin 之间的事件通信 (e.g. 训练完成时触发通知) — 已由 EventBus 处理, 插件可订阅
- 插件热加载 (无需重启即可添加新插件) — 系统项目无此需求
- 插件市场 / 远程安装 — 远超系统项目范围

---

## 九、Stage 4 总结

**Stage 4 全部完成, 1 天工作量 (符合计划).**

| 维度 | 评价 |
|---|---|
| 架构清晰度 | ⭐⭐⭐⭐⭐ plugin 与 app 解耦, 4 抽象接口边界明确 |
| 可扩展性 | ⭐⭐⭐⭐⭐ 新增插件 0 改 main.py, 业务代码按需切换 |
| 风险控制 | ⭐⭐⭐⭐⭐ 兼容现有 storage_service / celery_app, 不替换只包装 |
| 测试覆盖 | ⭐⭐⭐⭐ 11 个用例, 覆盖 4 分类 + 全部 API + FastAPI 路由 |
| 文档完整 | ⭐⭐⭐⭐⭐ 本报告 + 路线图更新 |

**Stage 4 实际收益**:
1. **新增插件 0 改 main.py**: `PluginRegistry.discover_plugins([...])` 一行加新分类
2. **业务代码替换存储后端零侵入**: 仅改 import 即可从 Local 切到 MinIO/S3
3. **ML 框架可插拔**: 新增 Ultralytics/Torchvision 后端不影响 timm 业务
4. **通知统一入口**: 未来扩展 Email/Webhook 仅需新增 `XxxNotificationPlugin`

**下一步**: 立即进入 Stage 5 (性能 + 监控), 1 天工作量, 涵盖启动顺序优化 + 业务缓存层 + 监控聚合.
