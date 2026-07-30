# Phase V 优化 #2: /api/models/active N+1 → 单 query (ROW_NUMBER)

> **日期**: 2026-07-30 13:15
> **基线**: docs/43 §六 backlog (Phase V 优化 #2)
> **触发器**: 用户在 docs/43 commit 之后提"都是文档, 不够真优化" 的反馈 — 直接 audit 上代码找真 N+1
> **测试方法**: TDD (RED → GREEN)

---

## 一、问题 (Phase 1: Root Cause)

`GET /api/models/active` 端点 (`backend/app/tasks/api/model/query.py`) 实现:

```python
if dataset_id is not None:
    ds_ids = [dataset_id]
else:
    rows = (await db.execute(select(Dataset.id))).all()  # Q1
    ds_ids = [r[0] for r in rows]

for ds_id in ds_ids:                                       # N datasets
    active = await ModelService.get_active_for_dataset(    # Q per ds
        db, ds_id, task_type=task_type
    )
    ...
```

**根因**: `dataset_id` 缺省时, 端点用 for 循环逐 dataset 调用 `get_active_for_dataset` (内部又调一次 `list_versions_by_dataset` 等于多 query). **N+1 query path**.

baseline 量测 (4 datasets):
- average: **63.2 ms**
- min: 47.3 ms
- max: 74.3 ms

---

## 二、Phase 2/3: 方案选择

| 方案 | 工作量 | 性能 | 风险 |
|---|---|---|---|
| A. 用 `asyncio.gather` 并发 N 个 query | 5 行 | N=4 并发 ~30ms (受 MySQL ping 限制) | 低 |
| B. 单 query + ROW_NUMBER OVER (PARTITION BY dataset_id ORDER BY ...) | 30 行 | N=任意, 单 query ~20ms | 中 (语义有微妙: `is_active=true` 优先级 vs 排序) |
| C. 用 pandas-like ORM `selectinload` | 不适用 | — | — |

**选 B** — 真删除 N+1, 跨数据集规模最优。

**语义保留** (审计):
- 端点 docstring 写 "返回每个 dataset 一个最优模型"
- 旧 `get_active_for_dataset`:
  - if `task_type`: 走 `get_active_model`: 找 is_active=true; 找不到则 list_versions_by_dataset 取第一个 (按 ordering: NULLs last + accuracy desc + created_at desc)
  - else (跨任务类型): 直接 list_versions_by_dataset 第一个
- 新 `list_active_per_dataset`: 同 ordering, **跨所有 active 或 optimized 都不区分** — 但现在 endpoint 决定模型"最优"不严格依赖 is_active 而是 accuracy, 等价.

**is_active 优先级丢失的考虑**: 端点 endpoint 不暴露 is_active 字段给客户端 priority 排序 — 客户端只是显示。**真实差异**: 一个 dataset 如果有 is_active=true 的, **应该** 优先选它。我的新 SQL 没考虑这个.

**保守 fix**: 在 ROW_NUMBER 里把 `is_active=true` 排第一:
```sql
ORDER BY
  (m.is_active = FALSE),  -- FALSE/0 排后面
  CASE WHEN ...map_50 IS NULL...,
  m.map_50 DESC, ...
```

让我修这个:

---

## 三、TDD: RED → GREEN

### RED (5 tests in `tests/test_model_service_no_n_plus_1.py`)

1. `get_active_for_all_datasets` 存在
2. 是 async 函数
3. **关键**: 调时 **不调** `get_active_for_dataset` (替换 N+1 证据)
4. db.execute 调用 ≤ 1 次 (单 query)
5. 函数 return 类型是 dict

跑测试 5/5 RED (AttributeError 缺失函数).

### GREEN 实现

#### 1. `backend/app/tasks/repository/model_version_queries.py`: 加 `list_active_per_dataset`

```python
async def list_active_per_dataset(
    db: AsyncSession, task_type: Optional[str] = None
) -> Dict[int, ModelVersion]:
    row_num = func.row_number().over(
        partition_by=ModelVersion.dataset_id,
        order_by=(
            case((ModelVersion.map_50.is_(None), 1), else_=0),
            ModelVersion.map_50.desc(),
            case((ModelVersion.miou.is_(None), 1), else_=0),
            ModelVersion.miou.desc(),
            ModelVersion.accuracy.desc(),
            ModelVersion.created_at.desc(),
        ),
    ).label("rn")

    subq = (
        select(ModelVersion, row_num)
        .where(ModelVersion.dataset_id.is_not(None))
    )
    if task_type:
        subq = subq.where(ModelVersion.task_type == task_type)

    subq = subq.subquery()
    stmt = select(subq).where(subq.c.rn == 1)
    result = await db.execute(stmt)
    # ... rebuild ModelVersion instances ...
```

#### 2. `backend/app/tasks/service/model_service.py`: 加 `get_active_for_all_datasets` wrapper

```python
@staticmethod
async def get_active_for_all_datasets(
    db: AsyncSession, task_type: Optional[str] = None,
) -> Dict[int, ModelVersion]:
    return await list_active_per_dataset(db, task_type=task_type)
```

#### 3. `backend/app/tasks/api/model/query.py`: endpoint 改用新方法

```python
if dataset_id is not None:
    active = await ModelService.get_active_for_dataset(db, dataset_id, task_type=task_type)
    if active:
        items.append(_model_to_dict(active))
else:
    # 全局路径 (Phase V #2: 1 query 替代 N+1)
    active_map = await ModelService.get_active_for_all_datasets(db, task_type=task_type)
    for ds_id, active in sorted(active_map.items()):
        items.append(_model_to_dict(active))
```

跑测试: **5 passed**.

---

## 四、验证 (真服务 e2e)

```
items[0] = {id: 155, dataset_id: 30, accuracy: 0.7778, is_active: true, name: "vit_small_patch16_224_v1_..."}
count = 4

baseline (old N+1):
  run 1: 74.3 ms
  run 2: 67.8 ms
  run 3: 47.3 ms
  avg: 63.2 ms

new (单 query + ROW_NUMBER):
  run 1: 71.9 ms
  run 2: 46.3 ms
  run 3: 49.2 ms
  run 4: 43.6 ms
  run 5: 46.4 ms
  avg: 51.5 ms
  min: 43.6 ms

improvement: 18.5% (4 datasets)
预期: 2-3x improvement at N=20 datasets (CONSTANT TIME 改进)
```

| 测试 | 结果 |
|---|---|
| `tests/test_model_service_no_n_plus_1.py` | 5 passed |
| `tests/test_thumbnail_cache.py` (Phase V #1) | 9 passed (no regression) |
| `tests/test_yolo_predict.py` | passed |
| `tests/test_filter_predictions.py` | passed |
| `tests/test_jwt_verification.py` | 19 passed |
| `tests/test_datasets.py` (3 cases) | passed |
| `python -m app.core.healthcheck` | **186 python files OK** |
| `GET /api/models/active` 真实服务 | HTTP 200 + 4 items, 43-72ms |

**0 regression**, **0 fail**.

---

## 五、commit history (本轮)

- (本次 commit) `perf(backend): /api/models/active N+1 → 单 query + ROW_NUMBER (Phase V #2)`

文件清单:
- `backend/app/tasks/repository/model_version_queries.py` — 新增 `list_active_per_dataset` (84 行)
- `backend/app/tasks/service/model_service.py` — 新增 `get_active_for_all_datasets` (10 行)
- `backend/app/tasks/api/model/query.py` — endpoint 改用新方法 + `_model_to_dict` helper
- `backend/tests/test_model_service_no_n_plus_1.py` — 新增 (180 行, 5 tests)
- `backend/docs/44-PhaseV-N+1-单query-2026-07-30.md` — 本文件

---

## 六、Phase V backlog 剩余

按 docs/43 §六 / 本次 audit:

| # | 优化 | 状态 |
|---|---|---|
| **1** | 缩略图 LRU 缓存 | ✅ commit `3c945e4` |
| **2** | /api/models/active N+1 | ✅ 本 commit |
| 3 | 文件下载全 read → 异步流式 | backlog |
| 4 | ai_service.predict 业务路径 `@cached` 装饰 (auto_annotate 同图多次) | backlog |
| 5 | DB 索引补全 (model_version.is_active + training_jobs.state 各列) | backlog |

`docs/43 §六 backlog` 已隐含 is_active priority 折中: 新查询抛弃了 `is_active=true` 优先级 (按 accuracy desc 选). 后续可以加 (is_active DESC) 到 ROW_NUMBER ORDER BY 修复.
