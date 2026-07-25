# Stage 2.7 实施完成报告 — main.py 改用 AppRegistry 自动挂载

**编制日期**: 2026-07-25
**版本**: v3.0.0 Stage 2.7
**关联文档**:
- [15-务实友好架构方案](./15-务实友好架构方案.md)
- [17-3层架构重构执行计划](./17-3层架构重构执行计划.md)
- [18-多应用架构优化方案](./18-多应用架构优化方案.md)
- [19-重构优先级与阶段路线图](./19-重构优先级与阶段路线图.md)
- [24-Stage2.6-实施完成报告](./24-Stage2.6-实施完成报告.md)

---

## 一、Stage 2.7 目标回顾

| 目标 | 状态 | 关键交付 |
|---|---|---|
| 4 个 App `get_routes()` 返回 RouteEntry 列表 | ✅ **完成** | admin 3 / auth 1 / tasks 9 / annotation 1 |
| main.py 用 `AppRegistry.iter_routes()` 自动挂载 | ✅ **完成** | 14 行手写 include_router → 1 个 for 循环 |
| lifespan 调 `AppRegistry.startup_all()` / `shutdown_all()` | ✅ **完成** | 4 个应用钩子 + 跨应用基础设施保留 |
| 99 路由全部存在 | ✅ **完成** | 0 路由增减, 完全等价 |
| 21 个关键路径全部命中 | ✅ **完成** | 0 路径漂移 |
| main.py 0 业务路由硬编码 | ✅ **完成** | 仅留 14 行注释说明"原写法" |

---

## 二、Stage 2.7 详细实施内容

### 2.1 抽象层增强

#### `app/common/interfaces.py` 新增 `RouteEntry` NamedTuple

```python
class RouteEntry(NamedTuple):
    """应用路由条目: (router, prefix, tags)"""
    router: APIRouter
    prefix: str = ""
    tags: List[str] = []
```

#### `AppInterface.get_routes()` 默认实现

```python
def get_routes(self) -> List[RouteEntry]:
    """默认: 单一 router, 无 prefix, 无 tags. 子类可 override 返回多前缀场景."""
    return [RouteEntry(router=self.router, prefix="", tags=[])]
```

### 2.2 4 个 App `__init__.py` 实现 `get_routes()`

| App | RouteEntry 数 | prefix 列表 |
|---|---|---|
| `AdminApp` | 3 | `/api/users`, `/api/stats`, `/api` (system 路由) |
| `AuthApp` | 1 | `/api/auth` |
| `TasksApp` | 9 | `/api/datasets`, `/api/images`, `/api/training`, `/api/models`, `/api/auto-annotate`, `/api/export`, `/api/detection`, `/api/segmentation`, `/api/files` |
| `AnnotationApp` | 1 | `/api/annotations` |
| **合计** | **14** | 14 个独立 prefix |

### 2.3 AppRegistry 新增 `iter_routes()`

```python
@classmethod
def iter_routes(cls) -> List[Tuple[str, "RouteEntry"]]:
    """获取所有应用的路由条目 (app_name, RouteEntry) 列表"""
    result: List[Tuple[str, "RouteEntry"]] = []
    for app in cls._apps.values():
        try:
            entries = app.get_routes()
        except Exception:
            logger.exception(f"AppRegistry: app '{app.name}' get_routes() failed, skip")
            continue
        for entry in entries:
            result.append((app.name, entry))
    return result
```

### 2.4 main.py 自动挂载

**前** (Stage 2.6):
```python
app.include_router(auth.router, prefix="/api/auth", tags=["用户认证"])
app.include_router(user.router, prefix="/api/users", tags=["用户管理"])
app.include_router(dataset.router, prefix="/api/datasets", tags=["数据集管理"])
app.include_router(image.router, prefix="/api/images", tags=["图像管理"])
app.include_router(annotation.router, prefix="/api/annotations", tags=["标注管理"])
app.include_router(auto_annotate.router, prefix="/api/auto-annotate", tags=["AI预标注"])
app.include_router(training.router, prefix="/api/training", tags=["模型训练"])
app.include_router(model_api.router, prefix="/api/models", tags=["模型管理"])
app.include_router(export.router, prefix="/api/export", tags=["标注导出"])
app.include_router(stats.router, prefix="/api/stats", tags=["统计分析"])
app.include_router(files.router, prefix="/api/files", tags=["文件服务"])
app.include_router(detection.router, prefix="/api/detection", tags=["目标检测"])
app.include_router(segmentation.router, prefix="/api/segmentation", tags=["图像分割"])
app.include_router(system.router, prefix="/api", tags=["系统"])
```

**后** (Stage 2.7):
```python
AppRegistry.discover_apps(["admin", "auth", "tasks", "annotation"])  # 模块级触发
...
for app_name, entry in AppRegistry.iter_routes():
    app.include_router(entry.router, prefix=entry.prefix, tags=entry.tags or None)
```

**收益**:
- 14 行手写代码 → 1 个 for 循环
- 新增业务应用零修改 main.py
- 调整路由 prefix / tags 零修改 main.py
- 自动日志记录 (每个挂载点一行 INFO)

### 2.5 lifespan 钩子增强

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动
    await init_db()
    configure_ultralytics()  # 跨应用基础设施
    migrate_legacy_yolo_weights()
    await AppRegistry.startup_all()  # Stage 2.7: 各应用 startup 钩子
    yield
    # 关闭
    await AppRegistry.shutdown_all()  # Stage 2.7: 各应用 shutdown 钩子
    await engine.dispose()  # 跨应用基础设施
    redis_client.close()
```

---

## 三、验证测试

### 3.1 编译验证 (py_compile)

```bash
- app/common/interfaces.py  ✅
- app/registry.py           ✅
- app/main.py               ✅
- app/{admin,auth,tasks,annotation}/__init__.py  ✅ (4 个)
```

### 3.2 运行时验证 (8 项检查, 全部通过)

```
[1] AppRegistry 注册情况
  Registered apps: ['admin', 'auth', 'tasks', 'annotation']

[2] iter_routes() 输出
  Total RouteEntry: 14
    - admin      prefix=/api/users         tags=['用户管理']
    - admin      prefix=/api/stats         tags=['统计分析']
    - admin      prefix=/api               tags=['系统']
    - auth       prefix=/api/auth          tags=['用户认证']
    - tasks      prefix=/api/datasets      tags=['数据集管理']
    - tasks      prefix=/api/images        tags=['图像管理']
    - tasks      prefix=/api/training      tags=['模型训练']
    - tasks      prefix=/api/models        tags=['模型管理']
    - tasks      prefix=/api/auto-annotate tags=['AI预标注']
    - tasks      prefix=/api/export        tags=['标注导出']
    - tasks      prefix=/api/detection     tags=['目标检测']
    - tasks      prefix=/api/segmentation  tags=['图像分割']
    - tasks      prefix=/api/files         tags=['文件服务']
    - annotation prefix=/api/annotations   tags=['标注管理']

[3] FastAPI app 加载
  Total routes: 99

[4] 关键路径验证
  OK: all 21 key paths exist

[5] RouteEntry 挂载一致性
  OK: 14 route groups all mounted

[6] 应用启动/关闭钩子
  admin      startup=True shutdown=True
  auth       startup=True shutdown=True
  tasks      startup=True shutdown=True
  annotation startup=True shutdown=True

[7] Celery 任务注册 (Stage 2.6 集成)
  OK: 7 tasks registered

[8] main.py 无业务路由硬编码
  OK: no hardcoded app.include_router(<router>.router) in main.py
  main.py uses AppRegistry.iter_routes() (count: 4)
```

### 3.3 端到端 FastAPI 加载

```python
from app.main import app
print(len([r for r in app.routes if hasattr(r, 'path')]))
# 99 (与 Stage 2.6 一致, 0 路由增减)
```

---

## 四、影响范围

### 4.1 受益场景

| 场景 | Stage 2.6 | Stage 2.7 |
|---|---|---|
| 新增业务应用 | main.py 需手写 include_router | main.py 0 修改, 仅需在 `__init__.py` 实现 get_routes() |
| 调整路由 prefix | 改 main.py 1 行 | 改 App `__init__.py` get_routes() |
| 移除某个应用 | 删 main.py 1 行 | 删 `app/<name>/` 整个目录, main.py 0 修改 |
| 应用数量增长 | main.py 越来越长 | main.py 永远 1 个 for 循环 |

### 4.2 保留的跨应用基础设施 (在 main.py)

- CORS 配置
- 异常处理 (AppException / Exception)
- 数据库 init
- 引擎释放 / Redis 释放
- ultralytics 路径配置
- Console-script entry

这些是"应用组合"的横切关注点, 属于 FastAPI 框架层, 不属于任何单个业务应用.

### 4.3 顺序保证

`AppRegistry.discover_apps()` 在 main.py 模块级 (FastAPI app 实例化之前) 调用, 确保:
1. `AppRegistry._apps` 在 `app.include_router()` 循环之前已填充完毕
2. `AppRegistry.startup_all()` 在 lifespan 内被 await, 顺序与注册顺序一致
3. `AppRegistry.shutdown_all()` 在 lifespan 内被 await, 顺序与注册反序 (符合 RAII 资源释放)

---

## 五、文件清单

### 5.1 修改文件 (Stage 2.7 实施)

```
backend/app/common/interfaces.py   (新增 RouteEntry + get_routes() 默认实现)
backend/app/registry.py            (新增 iter_routes() 辅助方法)
backend/app/main.py                (路由自动挂载 + lifespan 钩子)
backend/app/admin/__init__.py      (实现 get_routes() 返回 3 个 RouteEntry)
backend/app/auth/__init__.py       (实现 get_routes() 返回 1 个 RouteEntry)
backend/app/tasks/__init__.py      (实现 get_routes() 返回 9 个 RouteEntry)
backend/app/annotation/__init__.py (实现 get_routes() 返回 1 个 RouteEntry)
backend/docs/25-Stage2.7-实施完成报告.md (本文件)
```

合计: 7 个修改文件 + 1 个新增文件 = 8 个文件变更.

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
| Stage 2.6 (workers + ml 迁入 app.tasks) | ✅ | 已完成 |
| **Stage 2.7 (main.py 用 AppRegistry 自动挂载)** | ✅ | **本次完成** |
| Stage 2.8 (写报告 + 提交 + 删除旧目录) | ⏳ | 待实施 |
| Stage 3 (横切目录完善) | ⏳ | 按需 |
| Stage 4 (plugin/ 抽象接口) | ⏳ | 按需 |
| Stage 5 (性能 + 监控) | ⏳ | 按需 |

---

## 七、关键技术点

1. **RouteEntry NamedTuple**: 用 NamedTuple 而非 dataclass, 保证 `entry.router` / `entry.prefix` / `entry.tags` 解构/属性访问都支持, 序列化友好. NamedTuple 也是类型注解的天然载体.

2. **延迟 import 在 get_routes()**: 4 个 App 的 `get_routes()` 内都使用 `from app.{name}.api import X as Y` 延迟 import, 避免 `app.{name}/__init__.py` 在被 import 时就触发整个 API 子模块链. 这避免了循环导入风险 (e.g. `app.tasks.api.dataset` 中如果 import 了 `app.tasks.model` 而 `app.tasks.model` 又 import 了 `app.common` 等等).

3. **模块级 `discover_apps()` 调用**: `AppRegistry.discover_apps([...])` 在 main.py 模块级 (FastAPI app 实例化之前) 调用, 确保:
   - 后续 `iter_routes()` 能立即拿到已注册应用
   - 任何代码 (`from app.main import app`) 都会触发应用发现
   - 测试场景可以用 `AppRegistry.clear()` + 手动 `discover_apps()` 重置

4. **lifespan 中 startup/shutdown 顺序**: `startup_all()` 按注册顺序启动 (admin → auth → tasks → annotation), `shutdown_all()` 按注册反序关闭 (annotation → tasks → auth → admin), 符合 RAII 资源释放原则.

5. **跨应用基础设施与业务应用分离**: main.py 保留的 CORS / 异常处理 / 数据库 init / Redis 释放属于"应用组合层"的横切关注点, 不属于任何单个应用. `app/{name}/__init__.py` 中只声明本应用的路由和事件, 互不耦合. 这是多应用架构的核心原则.

6. **iter_routes() 健壮性**: `iter_routes()` 在调用 `app.get_routes()` 时用 try/except 包裹, 单个应用失败不会导致整个启动崩溃, 只会记录日志并 skip. 这对于插件化架构 (Stage 4) 的第三方应用失败时尤其重要.

---

## 八、Stage 2 全景回顾

| Stage | 主要内容 | 关键文件 | 状态 |
|---|---|---|---|
| 2.1 | AppRegistry + AppInterface | app/registry.py, app/common/interfaces.py | ✅ |
| 2.2 | 4 个 App skeleton | app/{admin,auth,tasks,annotation}/__init__.py | ✅ |
| 2.3 | ORM 模型迁入 app.model | app/{admin,tasks,annotation}/model/ | ✅ |
| 2.4 | service 层迁入 app.service | app/{admin,auth,tasks,annotation}/service/ | ✅ |
| 2.5 | API 路由迁入 app.api | app/{admin,auth,tasks,annotation}/api/ | ✅ |
| 2.6 | workers + ml 迁入 app.tasks | app/tasks/{ml,workers}/ | ✅ |
| **2.7** | **main.py 自动挂载** | **app/main.py** | **✅** |
| 2.8 | 删除旧目录 | (待实施) | ⏳ |

Stage 2 完成了"多应用 + 横切关注点 + 插件化接口"的多应用架构骨架. 下一阶段 (Stage 2.8) 将彻底清理 `app/api/`, `app/services/`, `app/model/`, `app/ml/`, `app/workers/` 等老目录, 完成 Stage 2 的收尾工作.
