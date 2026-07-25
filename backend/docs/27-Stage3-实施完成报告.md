# Stage 3 实施完成报告 — 横切目录完善

**编制日期**: 2026-07-25
**版本**: v3.0.0 Stage 3
**关联文档**:
- [15-务实友好架构方案](./15-务实友好架构方案.md)
- [18-多应用架构优化方案](./18-多应用架构优化方案.md)
- [19-重构优先级与阶段路线图](./19-重构优先级与阶段路线图.md)
- [26-Stage2.8-实施完成报告](./26-Stage2.8-实施完成报告.md)

---

## 一、Stage 3 目标回顾

| 目标 | 状态 | 关键交付 |
|---|---|---|
| `app/core/logging_setup.py` 集中日志配置 | ✅ **完成** | 控制台+文件双输出 / 第三方库降噪 / 滚动 10MB×5 |
| `RequestIDMiddleware` 跨服务追踪 | ✅ **完成** | X-Request-ID 透传 / ContextVar 跨 async / 自动生成 UUID4 |
| `RequestTimingMiddleware` 慢请求告警 | ✅ **完成** | X-Response-Time / WARNING >500ms / 配合 rid |
| `utils/datetime_utils.py` 时间工具 | ✅ **完成** | utc_now/to_iso/from_iso/humanize_duration/time_ago |
| `utils/file_utils.py` 文件工具 | ✅ **完成** | format_size/safe_filename/guess_mime_type/file_md5/file_sha256 |
| `common/constants.py` 集中常量 | ✅ **完成** | 训练状态机 + 通用限制 + Redis key 模板 + SSE 限制 |
| 5 个横切目录 (common/database/middleware/core/utils) 全部就位 | ✅ **完成** | 横切关注点彻底分离 |

---

## 二、Stage 3 详细实施内容

### 2.1 集中日志配置 (Stage 3.1)

**新增**: `app/core/logging_setup.py`

**功能**:
- 控制台 handler: 简短格式 (`timestamp | level | logger | message`)
- 文件 handler: 详细格式 (含文件路径 + 行号, 10MB 滚动 5 个备份)
- 第三方库自动降噪: uvicorn/sqlalchemy/celery/huggingface_hub/urllib3
- 路径自动锚定项目根 (避免 cwd 漂移)

**导出**:
- `setup_logging(log_dir, console_level, file_level)`: 初始化
- `get_logger(name)`: 获取命名 logger (统一入口)

**测试结果**: 0 异常, 日志写入 `logs/app.log` 成功

### 2.2 RequestIDMiddleware (Stage 3.2)

**新增**: `app/middleware/http/request_id.py`

**功能**:
- 透传前端 `X-Request-ID` header (前端 → 后端)
- 自动生成 `uuid4().hex` (32 字符, 后端 → 前端)
- 写入 `request.state.request_id` (FastAPI 路由可见)
- 写入 `ContextVar` (跨 async 调用的服务/工具可见)
- 响应 header 回传 (后端 → 前端)

**测试结果** (3/3 通过):
- ✓ 自动生成 32 字符 UUID4
- ✓ 透传前端传入的 X-Request-ID
- ✓ 5 个并发请求得到 5 个不同 ID

**导出**:
- `RequestIDMiddleware`: 中间件类
- `get_request_id()`: 从 ContextVar 读取当前请求 ID
- `set_request_id(rid)`: 手动设置 (供 worker 任务回写)
- `REQUEST_ID_HEADER`: header 名常量

### 2.3 RequestTimingMiddleware (Stage 3.3)

**新增**: `app/middleware/http/request_timing.py`

**功能**:
- 每个请求计算 `duration_ms`
- 写入响应 header `X-Response-Time`
- 慢请求 (>500ms) 输出 `WARNING [SLOW REQUEST] ...` 日志
- 所有请求输出 `DEBUG ...` 日志 (生产可关闭)
- 配合 `RequestIDMiddleware`, 日志附带 rid

**配置**:
- `.env` 中 `REQUEST_SLOW_THRESHOLD_MS=500` (默认 500ms)

**测试结果** (3/3 通过):
- ✓ 响应 header `X-Response-Time = "0.9ms"`
- ✓ 配置项 `REQUEST_SLOW_THRESHOLD_MS = 500`
- ✓ RequestTimingMiddleware 已注册到中间件栈

### 2.4 utils 工具 (Stage 3.4)

**新增**: `app/utils/datetime_utils.py` + `app/utils/file_utils.py`

**datetime_utils (6 函数)**:
- `utc_now()`: 当前 UTC 时间 (aware datetime)
- `to_iso(dt)`: datetime → ISO 8601 字符串
- `from_iso(s)`: ISO 8601 → datetime
- `humanize_duration(seconds)`: 秒数 → "1d 2h 3m 4s"
- `time_ago(dt)`: "刚刚" / "5 分钟前" / "2 天前"
- `ensure_utc(dt)`: 强制转换为 UTC

**file_utils (6 函数)**:
- `format_size(bytes)`: 字节数 → "1.5 MB"
- `safe_filename(name)`: 清理危险字符 (路径分隔符/特殊符号)
- `guess_mime_type(filename)`: 文件名 → MIME 类型
- `file_md5(path)`: 流式 MD5 (大文件友好, 8MB 块)
- `file_sha256(path)`: 流式 SHA256
- `ensure_dir(path)`: 确保目录存在 (含中间目录)

**测试结果** (3/3 通过):
- ✓ datetime 工具: roundtrip + duration 格式化 + time_ago
- ✓ file 工具: size 格式化 + 安全文件名 + MIME 推断
- ✓ ensure_dir + 流式 MD5 计算 (验证已知 hash)

### 2.5 common 常量 (Stage 3.5)

**新增**: `app/common/constants.py`

**状态机 (5 个 Enum)**:
- `TrainingState`: PENDING/PROGRESS/SUCCESS/FAILURE/REVOKED + `terminal()` helper
- `DatasetStatus`: DRAFT/READY/DELETED
- `ModelStatus`: TRAINING/READY/ACTIVE/ARCHIVED
- `AnnotationStatus`: UNCONFIRMED/CONFIRMED/CORRECTED
- `TaskTypeEnum`: CLASSIFICATION/DETECTION/SEGMENTATION

**通用限制 (12 常量)**:
- 分页: `DEFAULT_PAGE_SIZE=20` / `MAX_PAGE_SIZE=100`
- 上传: `MAX_UPLOAD_SIZE_MB=20` / `MAX_BATCH_UPLOAD_COUNT=50`
- 训练: `DEFAULT_TRAIN_EPOCHS=20` / `DEFAULT_TRAIN_BATCH_SIZE=32` / `DEFAULT_TRAIN_LEARNING_RATE=1e-4`
- 时长: `MAX_TRAIN_DURATION_HOURS=24` / `MAX_TRAIN_INACTIVITY_MINUTES=30`
- 置信度: `DEFAULT_AUTO_ANNOTATE_CONFIDENCE=0.6` / `MIN/MAX=0.1/0.99`

**Redis key 模板 (5 模板)**:
- `REDIS_KEY_TRAIN_HISTORY`: `train:history:{task_id}`
- `REDIS_KEY_TRAIN_PROGRESS`: `train:progress:{task_id}`
- `REDIS_KEY_TRAIN_ERROR`: `train:error:{task_id}`
- `REDIS_KEY_TRAIN_PAUSE`: `train:pause:{task_id}`
- `REDIS_KEY_TRAIN_ACTIVE`: `train:active:{user_id}`

**SSE 限制 (2 常量)**:
- `SSE_MAX_DURATION_SECONDS=1800` (30 分钟)
- `SSE_HEARTBEAT_INTERVAL_SECONDS=15`

**测试结果**: 22 个常量全部可正常导入, Redis key 模板 `.format(task_id="abc")` 工作正常

---

## 三、验证结果

### 3.1 Stage 2.8 综合验证 (回归测试)

```
========== Summary ==========
Passed: 6/6
  ✓ py_compile
  ✓ routes (99 路由不变)
  ✓ celery (7 任务注册)
  ✓ registry (4 apps)
  ✓ iter_routes (14 RouteEntry)
  ✓ no_old_paths
```

### 3.2 Stage 3 新功能验证

| 测试 | 结果 |
|---|---|
| `setup_logging()` 输出到 `logs/app.log` | ✅ |
| `RequestIDMiddleware` 3 项集成测试 | ✅ 3/3 |
| `RequestTimingMiddleware` 3 项集成测试 | ✅ 3/3 |
| `utils/datetime_utils` 3 项单元测试 | ✅ 3/3 |
| `utils/file_utils` 3 项单元测试 | ✅ 3/3 |
| `common/constants` 22 个常量导入 | ✅ 全部 |

### 3.3 99 路由不变

- 路由总数: 99 (与 Stage 2.8 持平)
- 中间件栈: CORS + RequestID + RequestTiming + (FastAPI 内置)
- 业务行为: 0 改动

---

## 四、最终横切目录结构 (Stage 3 完结)

```
backend/app/
├── common/                          # 横切 - 业务可复用组件
│   ├── base_model.py                # ORM 基类
│   ├── constants.py                 # ★ Stage 3 新增 - 集中常量
│   ├── enums.py                     # 业务枚举
│   ├── events.py                    # 事件总线
│   ├── exceptions.py                # 业务异常
│   ├── interfaces.py                # 抽象接口
│   ├── geometry/                    # 几何 (Stage 2.8)
│   ├── ml/                          # ML 通用 (Stage 2.8)
│   └── storage/                     # 存储抽象 (Stage 2.8)
├── core/                            # 横切 - 应用配置
│   ├── cli.py
│   ├── config.py                    # pydantic-settings (含 REQUEST_SLOW_THRESHOLD_MS)
│   ├── db_migration.py
│   ├── logging_setup.py             # ★ Stage 3 新增 - 集中日志
│   ├── redis_client.py
│   └── ultralytics_setup.py
├── database/                        # 横切 - 数据库配置
│   ├── engine.py
│   ├── session.py
│   └── __init__.py
├── middleware/                      # 横切 - 中间件
│   ├── http/
│   │   ├── auth.py
│   │   ├── cors.py
│   │   ├── error_handler.py
│   │   ├── request_id.py            # ★ Stage 3 新增
│   │   └── request_timing.py        # ★ Stage 3 新增
│   └── security/
│       └── security.py
├── schemas/                         # 请求/响应 Pydantic schema
├── utils/                           # 横切 - 工具函数
│   ├── async_helpers.py
│   ├── datetime_utils.py            # ★ Stage 3 新增
│   └── file_utils.py                # ★ Stage 3 新增
├── main.py                          # FastAPI 入口 (含 2 个新中间件)
├── registry.py                      # AppRegistry
└── ...
```

---

## 五、Stage 3 提交记录 (5 原子提交)

| # | 提交 | 说明 |
|---|---|---|
| 1 | `feat(stage3.1): core/logging_setup.py 集中日志配置` | 1 个新文件 + 1 个导出更新 |
| 2 | `feat(stage3.2): RequestIDMiddleware 注入/透传 X-Request-ID` | 1 个新文件 + 2 个文件更新 |
| 3 | `feat(stage3.3): RequestTimingMiddleware 慢请求告警` | 1 个新文件 + 3 个文件更新 |
| 4 | `feat(stage3.4): utils/datetime_utils.py + file_utils.py` | 2 个新文件 + 1 个导出更新 |
| 5 | `feat(stage3.5): common/constants.py 集中常量` | 1 个新文件 + 1 个导出更新 |
| 6 | `docs(stage3.6): Stage 3 实施完成报告` | 本文档 |

**总工作量**: ~0.5 天 (原计划 1.5 天, 节省 67%)

---

## 六、Stage 3 → Stage 4 衔接计划

| Stage | 内容 | 预计工作量 |
|---|---|---|
| **Stage 4** | 顶层 `plugin/` 目录 + 抽象 ML/Storage/TaskQueue 接口 | 1.5 天 |

**Stage 4 候选任务**:
- 创建 `plugin/` 顶层目录
- 实现 `plugin/storage_backends/local.py` (从 `app/common/storage/storage_service.py` 改写)
- 实现 `plugin/ml_backends/timm_classification.py` (从 `app/tasks/ml/classification.py` 抽接口)
- 实现 `plugin/task_queues/celery.py` (从 `app/tasks/workers/celery_app.py` 抽接口)
- 实现 `plugin/notification_channels/sse.py`
- 注册到 PluginRegistry (类似 AppRegistry)
- 业务代码只依赖接口, 不依赖具体实现
- 默认实现仍可用, 高级用户可替换插件

---

## 七、关键决策 (本阶段新增)

| 决策 ID | 主题 | 描述 |
|---|---|---|
| **DR-28** | 横切目录不依赖业务 | common/database/middleware/core/utils 严格不 import app/*, 业务代码单向依赖横切 |
| **DR-29** | Request ID 用 ContextVar | 用 contextvars.ContextVar 而非 request.state, 跨 async 边界自动传递, 适合深嵌套调用链 |
| **DR-30** | 慢请求阈值外置为配置 | `.env` 中 `REQUEST_SLOW_THRESHOLD_MS=500`, 避免硬编码, 不同环境可调 |
| **DR-31** | 常量集中而非分散 | 状态机/限制/Redis key 模板全部集中到 `common/constants.py`, 业务代码 0 硬编码 |
| **DR-32** | 日志路径锚定项目根 | 日志目录强制基于项目根解析, 避免 cwd 漂移污染 |

---

**维护人**: 后端开发组
**下一步**: Stage 4 — 创建 plugin/ 顶层 + 抽象 ML/Storage/TaskQueue 接口
