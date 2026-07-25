# Phase R 后端 API 拆分完成报告 — model.py → model/ 包

**日期**: 2026-07-25
**版本**: v3.0.0 Phase R
**范围**: `backend/app/tasks/api/model.py` 拆分

---

## 一、拆分动机

`model.py` 原有 503 行,承担 3 类职责:

1. **查询类** (list/detail/active) — 3 个路由
2. **激活类** (单/批激活/取消) — 3 个路由 + 1 schema
3. **删除类** (单/批删除 + 文件清理) — 2 个路由 + 1 schema + 1 工具函数

按照既定的"单一职责 + 按职责拆分 API"原则 (与 Phase N/O/P 保持一致), 拆为 `model/` 包, 3 个子模块 + 1 个 `__init__.py`。

---

## 二、拆分前后对比

| 维度 | 拆分前 | 拆分后 |
|---|---|---|
| 文件数 | 1 (model.py, 503 行) | 4 (model/{__init__,query,activation,deletion}.py) |
| 最大单文件行数 | 503 | 314 (query.py) |
| 单文件职责数 | 3 (查询/激活/删除) | 1 (每文件单一职责) |
| 死代码 | 1 (`_lock_dataset_models`, 未被调用) | 0 (已删除) |
| 路由数 | 9 | 9 (完全不变) |
| 全局 app 路由总数 | 105 | 105 (完全不变) |

---

## 三、新文件结构

```
backend/app/tasks/api/model/
├── __init__.py        37 行  — 拼装 router + 文档
├── query.py          191 行  — list/list_active/detail (3 路由)
├── activation.py     139 行  — activate/deactivate/batch_set_active (3 路由 + BatchActivateRequest)
└── deletion.py       188 行  — delete/batch_delete (2 路由 + BatchDeleteRequest + _delete_one_model 工具)
```

总计: 555 行 (略增, 主要是新增的模块文档字符串, 业务代码无新增)

---

## 四、各子模块职责边界

### 1. `query.py` — 模型版本查询
- **路由** (3):
  - `GET /` (兼容 `/api/models` 和 `/api/models/`)
  - `GET /active`
  - `GET /{model_id}/detail`
- **关键逻辑**:
  - 列表 + 详情字段透出分类/检测/分割三套指标
  - 详情额外返回 training_log / confusion_matrix / file_path
  - 委托 `ModelService.get_active_for_dataset()` 取得每 dataset 最佳模型
  - 避免 N+1: 预查 `dataset_name`

### 2. `activation.py` — 模型版本激活/取消激活
- **路由** (3):
  - `POST /{model_id}/activate` (委托 `ModelService.activate()`)
  - `POST /{model_id}/deactivate` (委托 `ModelService.deactivate()`)
  - `POST /batch-activate` (事务 + `with_for_update()` 行锁)
- **Schema**: `BatchActivateRequest`
- **关键逻辑**:
  - 单激活不变量: 同 dataset 同一时刻最多 1 个 active 模型 (Service 层保证)
  - 幂等: 已是目标状态也返回 success=True
  - 批量: 同一事务, 全部成功或全部回滚

### 3. `deletion.py` — 模型版本删除
- **路由** (2):
  - `DELETE /{model_id}` (单个删除 + training_job 解绑)
  - `POST /batch-delete` (批量删除 + 同一事务)
- **Schema**: `BatchDeleteRequest`
- **工具函数**: `_delete_one_model()` (磁盘文件清理)
- **关键逻辑**:
  - 允许删除当前已激活的版本 (删除即取消激活)
  - 解绑 `training_jobs.model_version_id` 引用 (保留训练历史)
  - 文件删除安全保护: 仅当路径在 `models/` 目录下 + 无其他版本引用时, 才删权重文件
  - 批量原子性: 任一失败则全部回滚

---

## 五、关键技术决策

### 决策 1: query_router 作为顶层 router

**问题**: FastAPI 不允许 `parent_router(prefix="")` 中 `include_router(child_router)` 时, child_router 含有 `path=""` 的路由 (会抛 `FastAPIError: Prefix and path cannot be both empty`)。

**原因**: `list_models` 路由为了兼容 `/api/models` 和 `/api/models/` 两个 URL, 同时挂了 `@router.get("")` 和 `@router.get("/")` 两个装饰器, 因此 query_router 中存在 `path=""` 路由。

**方案对比**:

| 方案 | 问题 | 选择 |
|---|---|---|
| A. 改 list_models 路径 (如 `/list`) | URL 改变, 不向后兼容 | ❌ |
| B. 创建中间 router 后再次 include | 触发空 prefix + 空 path 错误 | ❌ |
| C. 删除 `@router.get("")`, 只留 `/` | `/api/models` 访问会 404 | ❌ |
| **D. 让 query_router 作为顶层 router** | **path="" 在 query_router 自己, 不属于 include_router 的"父 prefix + 子 path"场景, 合法** | ✅ |

**实施**: `__init__.py` 中:
```python
router = query_router  # 不再二次 include, query_router 本身就是顶层
router.include_router(activation_router)  # activation/deletion 的 path 都是 "/{xxx}" 形式, 不会触发空 path 错误
router.include_router(deletion_router)
```

### 决策 2: 删除 `_lock_dataset_models` 死代码

**原因**: `batch_set_active` 路由实际用的是 `select(...).where(id.in_(uniq_ids)).with_for_update()`, 并未调用 `_lock_dataset_models` 工具函数。`grep -r "_lock_dataset_models" app/` 确认无任何调用方。

**实施**: 直接删除, 不放入 deletion.py 等其他子模块, 避免引入未使用的工具函数。

### 决策 3: Pydantic Schema 就近放置

**原因**: `BatchDeleteRequest` 仅在 deletion.py 中使用, `BatchActivateRequest` 仅在 activation.py 中使用。按照"路由同文件就近定义"原则, 避免额外创建 `schemas.py`。

---

## 六、向后兼容性验证

### 6.1 路由路径完全不变 (9 个)

| 路径 | 方法 | 拆分前 | 拆分后 |
|---|---|---|---|
| `/api/models` | GET | ✅ | ✅ |
| `/api/models/` | GET | ✅ | ✅ |
| `/api/models/active` | GET | ✅ | ✅ |
| `/api/models/{model_id}/detail` | GET | ✅ | ✅ |
| `/api/models/{model_id}/activate` | POST | ✅ | ✅ |
| `/api/models/{model_id}/deactivate` | POST | ✅ | ✅ |
| `/api/models/batch-activate` | POST | ✅ | ✅ |
| `/api/models/{model_id}` | DELETE | ✅ | ✅ |
| `/api/models/batch-delete` | POST | ✅ | ✅ |

### 6.2 app 总路由数验证
- 拆分前: `len(app.routes) = 105`
- 拆分后: `len(app.routes) = 105` ✅ 一致

### 6.3 模块导出兼容
- `app.tasks.api.model.router` ✅ (供 tasks/api/__init__.py import)
- `app.tasks.api.model.model_router` ✅ (兼容旧别名)
- `app.tasks.api.model.query_router` ✅
- `app.tasks.api.model.activation_router` ✅
- `app.tasks.api.model.deletion_router` ✅

---

## 七、验证步骤

1. **py_compile 通过** ✅
   ```
   $ python -m compileall -q app/
   (无输出 = 全部通过)
   ```

2. **App 启动 + 路由挂载验证** ✅
   ```
   Apps: {'admin': 'v3.0.0', 'auth': 'v3.0.0', 'tasks': 'v3.0.0', 'annotation': 'v3.0.0'}
   Total routes in app: 105
   Model API routes (9):
     GET     /api/models
     GET     /api/models/
     GET     /api/models/active
     POST    /api/models/batch-activate
     POST    /api/models/batch-delete
     DELETE  /api/models/{model_id}
     POST    /api/models/{model_id}/activate
     POST    /api/models/{model_id}/deactivate
     GET     /api/models/{model_id}/detail
   ```

3. **子 router 独立路由数** ✅
   - query_router: 9 (含 path="" 和 path="/" 的 list_models)
   - activation_router: 3
   - deletion_router: 2

4. **死代码检查** ✅
   - `_lock_dataset_models` 已删除, 无调用方
   - `model.py` 文件已删除, 无残留 import

---

## 八、提交记录

- `chore(phaseR): 同步删除旧 model.py (与 model/ 包同名冲突)`
- `refactor(phaseR): model.py 拆分为 model/ 包 (3 子模块, 503 → 4 文件)`
- `docs(phaseR): model 拆分完成报告 (Phase R)`

---

## 九、累计完成情况

| 阶段 | 范围 | 行数 | 状态 |
|---|---|---|---|
| Phase L | useDatasetDetail / TaskType 导出 | - | ✅ |
| Phase M | DetectionAnnotator.vue 重构 (1050 → 333) | -82% | ✅ |
| Phase N | image.py 拆分 (1152 → 6 文件) | 拆 5 子模块 | ✅ |
| Phase O | training.py 拆分 (1019 → 6 文件) | 拆 5 子模块 | ✅ |
| Phase P | detection.py 拆分 (980 → 5 文件) | 拆 4 子模块 | ✅ |
| **Phase R** | **model.py 拆分 (503 → 4 文件)** | **拆 3 子模块** | **✅** |

后端 API 拆分工作全部完成, 4 个超 500 行的 API 文件 (image/training/detection/model) 已全部拆分为包结构, 单文件职责单一, 路由完全向后兼容。
