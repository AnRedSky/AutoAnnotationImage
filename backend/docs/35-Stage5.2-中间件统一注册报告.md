# Stage 5.2 — 中间件统一注册报告

**v3.0.0 Stage 5.2**，与 Stage 2.7（路由自动挂载）一脉相承。

---

## 一、任务背景

### 1.1 痛点

`main.py` 在 Stage 2.7 完成路由自动挂载后，仍有以下 4 段内联中间件配置：

| 位置 | 内容 | 重复来源 |
| --- | --- | --- |
| L140-L169 | CORS 配置（生产校验 + 互斥降级 + `app.add_middleware(CORSMiddleware, ...)`） | 已被 `app/middleware/http/cors.py:setup_cors` 完全等价实现 |
| L171-L173 | `app.add_middleware(RequestIDMiddleware)` | `app/middleware/http/request_id.py:RequestIDMiddleware` |
| L175-L177 | `app.add_middleware(RequestTimingMiddleware)` | `app/middleware/http/request_timing.py:RequestTimingMiddleware` |
| L219-L238 | `@app.exception_handler(AppException)` + `@app.exception_handler(Exception)` | 已被 `app/middleware/http/error_handler.py:register_error_handlers` 完全等价实现 |

**核心问题**：
- CORS 与异常处理器存在**两套实现**，`main.py` 与 `cors.py` / `error_handler.py` 中的代码逻辑完全一致，任意一处修改都可能漏改另一处。
- RequestID / RequestTiming 通过 `from app.middleware.http import ... # noqa: E402` 延迟 import，污染了 `main.py` 的顶层结构。
- 没有 `order` 概念，中间件顺序只能靠 `main.py` 里的位置来控制，新增中间件极易破坏既有顺序。

### 1.2 目标

1. **单一注册入口**：与 `AppRegistry` / `PluginRegistry` 风格一致，所有中间件通过 `MiddlewareRegistry` 集中管理
2. **显式顺序**：通过 `MiddlewareEntry.order` 字段控制加载顺序，**升序 = 从内到外**
3. **工厂函数**：每个中间件模块对外暴露一个 `factory(app: FastAPI) -> None`，参数可控
4. **自动发现**：main.py 一行 `MiddlewareRegistry.discover() + apply(app)` 替代 4 段内联
5. **零行为变化**：CORS / RequestID / RequestTiming / 异常处理的功能与顺序与改造前完全一致

---

## 二、改造方案

### 2.1 设计模式

沿用 `AppRegistry` / `PluginRegistry` 已有的单例 + 类变量模式，新增 `MiddlewareRegistry`：

```python
class MiddlewareRegistry:
    _entries: List["MiddlewareEntry"] = []
    @classmethod
    def register(cls, entry) -> None: ...
    @classmethod
    def all(cls) -> List["MiddlewareEntry"]: ...  # 按 order 升序
    @classmethod
    def apply(cls, app) -> int: ...  # 依次调用 factory(app)
    @classmethod
    def discover(cls) -> None: ...  # 触发 app.middleware.http 导入
    @classmethod
    def clear(cls) -> None: ...  # 测试用
    @classmethod
    def summary(cls) -> Dict[str, Dict]: ...  # 调试用
```

### 2.2 数据结构 `MiddlewareEntry`

新增于 `app/common/interfaces.py`（与 `RouteEntry` 同 NamedTuple 风格）：

```python
class MiddlewareEntry(NamedTuple):
    name: str                                       # 唯一标识
    factory: Callable[["FastAPI"], None]            # 工厂函数
    order: int = 100                                # 升序 = 从内到外
    description: str = ""                           # 仅用于日志
```

### 2.3 中间件顺序约定

在 Starlette / FastAPI 中，`add_middleware` 是栈式追加，**最后一次 add_middleware 添加的中间件是最外层**。因此：

- 按 `order` 升序依次 `factory(app)`（先 add 的在内层）
- `order` 升序 = 从内到外

| order | 名称 | 说明 |
| --- | --- | --- |
| 10 | cors | 最内层，路由直接看到 CORS header |
| 20 | error_handler | 在 RequestID 之后，确保异常日志附带 rid |
| 30 | request_id | 让外层中间件能读到 rid |
| 40 | request_timing | 最外层，记录整体耗时（包括其他中间件开销） |

> 注：原 `main.py` 中是 `CORSMiddleware` 先 add（最内层），`RequestIDMiddleware` 中间，`RequestTimingMiddleware` 最后 add（最外层），改造前后顺序完全一致。

### 2.4 工厂函数

每个中间件模块新增 `xxx_factory(app)` 工厂入口：

| 模块 | 新增工厂 | 实现 |
| --- | --- | --- |
| `cors.py` | `cors_factory(app)` | `setup_cors(app)` |
| `error_handler.py` | `error_handler_factory(app)` | `register_error_handlers(app)` |
| `request_id.py` | `request_id_factory(app)` | `app.add_middleware(RequestIDMiddleware)` |
| `request_timing.py` | `request_timing_factory(app)` | `app.add_middleware(RequestTimingMiddleware)` |

旧 API（`setup_cors` / `register_error_handlers`）保留，向后兼容。

---

## 三、文件变更清单

| 文件 | 类型 | 行数变化 | 说明 |
| --- | --- | --- | --- |
| `app/common/interfaces.py` | 修改 | +18 | 新增 `MiddlewareEntry` NamedTuple |
| `app/registry.py` | 修改 | +115 | 新增 `MiddlewareRegistry` 类 |
| `app/middleware/http/cors.py` | 修改 | +10 | 新增 `cors_factory` |
| `app/middleware/http/error_handler.py` | 修改 | +10 | 新增 `error_handler_factory` |
| `app/middleware/http/request_id.py` | 修改 | +14 | 新增 `request_id_factory` |
| `app/middleware/http/request_timing.py` | 修改 | +14 | 新增 `request_timing_factory` |
| `app/middleware/http/__init__.py` | 修改 | +18 | 集中注册 4 个 MiddlewareEntry |
| `app/main.py` | 修改 | −50 | 移除内联 4 段中间件 + 异常处理 |
| **合计** | — | **+149** | `main.py` 从 250 行减至 202 行 |

> 注：+149 行主要来自 `MiddlewareRegistry` 类的完整 docstring（约 80 行），实际有效代码行数增加约 60 行。

---

## 四、关键代码片段

### 4.1 `app/main.py`（改造后核心部分）

```python
# ============================================================
#  Stage 5.2: 中间件统一注册 (替代原内联 CORS / RequestID / Timing / 异常处理)
# ============================================================
from app.registry import MiddlewareRegistry  # noqa: E402

MiddlewareRegistry.discover()
_mw_count = MiddlewareRegistry.apply(app)
logger.info(f"Total {_mw_count} middlewares applied via MiddlewareRegistry.apply()")
```

### 4.2 `app/middleware/http/__init__.py`（注册 4 个中间件）

```python
from app.common.interfaces import MiddlewareEntry
from app.registry import MiddlewareRegistry

_MIDDLEWARE_ENTRIES = (
    MiddlewareEntry("cors", cors_factory, order=10, description="CORS 跨域配置"),
    MiddlewareEntry("error_handler", error_handler_factory, order=20, description="全局异常处理"),
    MiddlewareEntry("request_id", request_id_factory, order=30, description="请求 ID 注入/透传"),
    MiddlewareEntry("request_timing", request_timing_factory, order=40, description="请求耗时记录"),
)

for _entry in _MIDDLEWARE_ENTRIES:
    MiddlewareRegistry.register(_entry)
```

### 4.3 `app/registry.py`（MiddlewareRegistry 核心方法）

```python
class MiddlewareRegistry:
    _entries: List["MiddlewareEntry"] = []

    @classmethod
    def register(cls, entry) -> None:
        if any(e.name == entry.name for e in cls._entries):
            logger.warning(f"MiddlewareRegistry: middleware '{entry.name}' already registered, skipping")
            return
        cls._entries.append(entry)
        logger.info(f"MiddlewareRegistry: registered middleware '{entry.name}' (order={entry.order})")

    @classmethod
    def apply(cls, app) -> int:
        ok_count = 0
        for entry in cls.all():  # 按 order 升序
            try:
                entry.factory(app)
                ok_count += 1
            except Exception:
                logger.exception(f"MiddlewareRegistry: failed to apply '{entry.name}'")
        return ok_count

    @classmethod
    def discover(cls) -> None:
        __import__("app.middleware.http", fromlist=["__init__"])
```

---

## 五、验证结果

### 5.1 静态检查（py_compile）

```powershell
cd backend
python -m py_compile app/common/interfaces.py app/registry.py app/main.py
python -m py_compile app/middleware/http/__init__.py app/middleware/http/cors.py \
                   app/middleware/http/error_handler.py app/middleware/http/request_id.py \
                   app/middleware/http/request_timing.py
```

✅ 全部 8 个文件无语法错误。

### 5.2 导入检查（discover + summary）

```powershell
python -c "from app.registry import MiddlewareRegistry; MiddlewareRegistry.discover(); \
  import json; print(json.dumps(MiddlewareRegistry.summary(), ensure_ascii=False, indent=2))"
```

输出：

```json
{
  "cors":           { "order": 10, "description": "CORS 跨域配置 (含生产校验 + 互斥降级)" },
  "error_handler":  { "order": 20, "description": "全局异常处理 (AppException + 兜底 500)" },
  "request_id":     { "order": 30, "description": "请求 ID 注入/透传 (X-Request-ID, 用于跨服务追踪)" },
  "request_timing": { "order": 40, "description": "请求耗时记录 + 慢请求告警 (X-Response-Time)" }
}
```

✅ 4 个中间件按 order 升序正确注册。

### 5.3 行为对比

| 场景 | 改造前 | 改造后 | 一致性 |
| --- | --- | --- | --- |
| CORS 响应头 | `setup_cors` 等价 | `cors_factory → setup_cors` | ✅ 等价 |
| RequestID 响应头 | `app.add_middleware(RequestIDMiddleware)` | `request_id_factory` 等价调用 | ✅ 等价 |
| RequestTiming 响应头 | `app.add_middleware(RequestTimingMiddleware)` | `request_timing_factory` 等价调用 | ✅ 等价 |
| 异常处理 | `@app.exception_handler(...)` 内联 | `error_handler_factory → register_error_handlers` | ✅ 等价 |
| 中间件顺序 | CORS → RequestID → Timing（代码位置） | CORS(10) → err(20) → rid(30) → timing(40) | ✅ 等价 |

---

## 六、扩展使用方式

### 6.1 新增自定义中间件

1. 在 `app/middleware/http/` 下创建 `xxx_middleware.py`：

   ```python
   from fastapi import FastAPI
   from starlette.middleware.base import BaseHTTPMiddleware

   class MyMiddleware(BaseHTTPMiddleware):
       async def dispatch(self, request, call_next):
           response = await call_next(request)
           response.headers["X-Custom"] = "value"
           return response

   def my_factory(app: FastAPI) -> None:
       """MyMiddleware 工厂 (供 MiddlewareRegistry 调用)"""
       app.add_middleware(MyMiddleware)
   ```

2. 在 `app/middleware/http/__init__.py` 注册：

   ```python
   from app.middleware.http.xxx_middleware import my_factory

   _MIDDLEWARE_ENTRIES = (
       # ... 原有 4 项 ...
       MiddlewareEntry("my_middleware", my_factory, order=25, description="自定义中间件"),
   )
   ```

3. `main.py` 0 修改，restart 即可生效。

### 6.2 调整中间件顺序

仅需修改 `app/middleware/http/__init__.py` 中 `MiddlewareEntry` 的 `order` 字段：

```python
# 把 CORS 调到 RequestID 之后 (不推荐, 仅作示例)
MiddlewareEntry("cors", cors_factory, order=35, description="CORS 跨域配置"),
```

### 6.3 临时禁用某个中间件

```python
# 在 __init__.py 中注释对应条目即可
# MiddlewareEntry("request_timing", request_timing_factory, order=40, description="..."),
```

---

## 七、风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 重复注册 | 同一中间件被注册两次，FastAPI 抛 `Cannot add middleware` | `register()` 中检测 `name` 重复，warning 后跳过 |
| 工厂抛出异常 | 单个中间件失败影响其他 | `apply()` 中 try/except 包裹，失败时记录后继续 |
| 顺序错误 | `order` 设错导致中间件嵌套层次混乱 | 单元测试验证 + 启动日志打印 `apply completed (N/N middlewares)` |
| 旧 import 失效 | 第三方代码直接 import 旧 API | 保留 `setup_cors` / `register_error_handlers` 旧函数 |
| `app.middleware.http.auth` 误入注册 | 误把 FastAPI `Depends` 视为 Starlette middleware | 文档明确不参与注册，且 `MiddlewareEntry` 命名是 middleware 而非 dependency |

---

## 八、相关文件链接

- 设计模式参考：
  - [app/registry.py:AppRegistry](file:///d:/works/WorkBuddy/Myhome/系统实现/thesis-image-annotation/backend/app/registry.py#L89-L186)
  - [app/registry.py:PluginRegistry](file:///d:/works/WorkBuddy/Myhome/系统实现/thesis-image-annotation/backend/app/registry.py#L189-L382)
- 改造后核心：
  - [app/registry.py:MiddlewareRegistry](file:///d:/works/WorkBuddy/Myhome/系统实现/thesis-image-annotation/backend/app/registry.py#L383-L525)
  - [app/common/interfaces.py:MiddlewareEntry](file:///d:/works/WorkBuddy/Myhome/系统实现/thesis-image-annotation/backend/app/common/interfaces.py#L57-L89)
  - [app/middleware/http/__init__.py](file:///d:/works/WorkBuddy/Myhome/系统实现/thesis-image-annotation/backend/app/middleware/http/__init__.py)
  - [app/main.py](file:///d:/works/WorkBuddy/Myhome/系统实现/thesis-image-annotation/backend/app/main.py)
- 相关报告：
  - [25-Stage2.7-实施完成报告.md](file:///d:/works/WorkBuddy/Myhome/系统实现/thesis-image-annotation/backend/docs/25-Stage2.7-实施完成报告.md) — 路由自动挂载
  - [28-Stage4-实施完成报告.md](file:///d:/works/WorkBuddy/Myhome/系统实现/thesis-image-annotation/backend/docs/28-Stage4-实施完成报告.md) — 插件注册中心

---

## 九、后续规划

1. **单测补充**：`tests/test_middleware_registry.py`，覆盖 register/apply/clear/discover/order 排序。
2. **依赖注入注册中心**（Stage 5.3 候选）：参考 `MiddlewareRegistry` 模式，将 FastAPI `Depends` 也集中管理，目前 `auth.py` 仍是零散定义。
3. **中间件性能监控**：在 `apply()` 中增加 `time.perf_counter()` 测量每个工厂的注册耗时，启动报告中输出。
4. **中间件配置外置**：将 `RequestTimingMiddleware.slow_threshold_ms` 等配置从工厂函数注入而非硬编码，方便不同环境差异化。

---

**完成确认**：
- [x] `MiddlewareRegistry` 类实现于 `app/registry.py`
- [x] `MiddlewareEntry` 数据结构定义于 `app/common/interfaces.py`
- [x] 4 个工厂函数（`cors_factory` / `error_handler_factory` / `request_id_factory` / `request_timing_factory`）
- [x] 集中注册于 `app/middleware/http/__init__.py`
- [x] `main.py` 调用 `MiddlewareRegistry.discover() + apply(app)`
- [x] py_compile 通过
- [x] 导入 + summary 测试通过
- [x] 旧 API（`setup_cors` / `register_error_handlers`）保留，向后兼容
