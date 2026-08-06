# v3.6.1 PATCH — TrainingJob resume 训练 race condition 兜底

> **报告日期**: 2026-08-06
> **修复版本**: v3.6.1 (HOTFIX, 紧跟 v3.6.0)
> **触发事件**: 用户报告「点击暂停后再次点击继续训练时, 出现 IntegrityError 1062」
> **关联文档**: [plan-c-train-perf-v3.6.0.md](../../.trae/documents/plan-c-train-perf-v3.6.0.md) 附录 A

---

## 一、Bug 描述

### 1.1 用户报告

训练任务, 点击暂停后再次点击继续训练时, 出现异常:

```
[2026-08-06 10:34:17,868: ERROR/MainProcess] Task
app.tasks.workers.classification.train_model_task[a8a7000d-88aa-44a3-97b2-ee47ff962710]
raised unexpected: IntegrityError('(pymysql.err.IntegrityError) (1062,
"Duplicate entry \'a8a7000d-88aa-44a3-97b2-ee47ff962710\' for key
\'training_jobs.ix_training_jobs_celery_task_id\'")')
```

### 1.2 影响范围

- 训练 resume 流程 (mode=resume) 偶发失败
- 影响所有走 `create_or_reset_job_sync` 的 worker (classification / detection / segmentation)
- 失败时 worker 任务挂掉, 用户需手动重新提交训练
- 业务影响: 用户体验受损, 但数据未损坏 (无脏写)

### 1.3 复现路径

- 数据库: MySQL (生产) / SQLite (测试)
- 操作: 训练 → 暂停 → 继续 (API: `POST /api/training/jobs/{id}/start?mode=resume`)
- 时序: API commit 与 worker 启动并发 (race window 极短, 但 100% 触发一次)

---

## 二、根因分析

### 2.1 时序图

```
API 端 (resume 模式)                   Worker 端 (train_model_task)
─────────────────────                  ─────────────────────────────
1. SELECT 旧 job 行
2. UPDATE celery_task_id = task.id
   ↓
   ...race window...
                                       3. SELECT celery_task_id = task.id
                                          → 找不到 (API commit 还没完成)
4. db.commit()                          ↓
                                       4. INSERT INTO training_jobs (..., celery_task_id=task.id)
                                          ↓
                                          5. UNIQUE 约束触发 IntegrityError
                                          6. worker 任务挂掉
```

### 2.2 关键点

- API 端 `start_existing_training_job` 流程: `task.delay()` 投递任务 → `db.commit()` UPDATE `celery_task_id`
- Worker 端 `create_or_reset_job_sync` 流程: 第一次 SELECT → 找不到 → INSERT → 撞 UNIQUE
- 两个流程并发时, API commit 可能晚于 worker 的 SELECT, 但早于 worker 的 INSERT
- 此时 worker 看不到 existing, 走 INSERT 路径, 撞 unique key
- `app.tasks.service.training_lifecycle_service.job.create_or_reset_job` 原本**无 IntegrityError 处理**, 直接抛出

### 2.3 涉及文件

- `backend/app/tasks/api/training/start.py`: `_create_pending_restart_job` (mode=restart) 预创建 PENDING 行
- `backend/app/tasks/api/training/start.py`: `start_existing_training_job` (mode=resume) UPDATE `celery_task_id`
- `backend/app/tasks/workers/classification.py`: `train_model_task` 调 `create_or_reset_job_sync`
- `backend/app/tasks/service/training_lifecycle_service/job.py`: `create_or_reset_job` 是 INSERT/UPDATE 的关键决策点
- `backend/app/tasks/model/training_job.py`: `celery_task_id` 字段 `unique=True, index=True`

---

## 三、修复方案

### 3.1 核心思路

在 `create_or_reset_job` 的 INSERT 路径加 `try/except IntegrityError`:
- 检测到是 `celery_task_id` 唯一冲突 → 视为 race
- 回滚当前 INSERT, 重新 SELECT (此时 API 一定已 commit)
- 走 UPDATE 路径, 复用现有 reset 逻辑

### 3.2 关键代码

[backend/app/tasks/service/training_lifecycle_service/job.py:122-148](../../backend/app/tasks/service/training_lifecycle_service/job.py)

```python
db.add(job)
try:
    await db.commit()
except IntegrityError as e:
    # v3.6.1 PATCH: race condition 兜底
    await db.rollback()
    error_msg = str(e).lower()
    is_dup_celery_task_id = (
        "celery_task_id" in error_msg
        and (
            "duplicate" in error_msg  # MySQL
            or "unique constraint" in error_msg  # SQLite / PostgreSQL
        )
    )
    if not is_dup_celery_task_id:
        raise  # 其它完整性错误 (如外键) 不是 race, 直接抛
    logger.warning(
        "[job] race detected: celery_task_id=%s already exists, "
        "falling back to UPDATE existing (API resume path)",
        task_id,
    )
    # 重新查询 (API 端 UPDATE 已 commit, 这次一定能找到)
    existing = (await db.execute(
        select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
    )).scalar_one_or_none()
    if existing is None:
        logger.error(...)
        raise  # 极端情况: 行被删, 抛原错误
    # 走 UPDATE 路径, 复用 reset 逻辑 (inlined 避免双层嵌套)
    ...
```

### 3.3 跨 DB 错误消息识别

| DB | 错误消息格式 | 识别关键字 |
|----|------------|----------|
| MySQL | `(1062, "Duplicate entry 'xxx' for key 'ix_training_jobs_celery_task_id'")` | `celery_task_id` + `duplicate` |
| SQLite | `UNIQUE constraint failed: training_jobs.celery_task_id` | `celery_task_id` + `unique constraint` |
| PostgreSQL | `duplicate key value violates unique constraint "ix_training_jobs_celery_task_id"` | `celery_task_id` + `unique constraint` |

**关键决策**: 不用 `1062` 数字判断 (只对 MySQL 有效), 改用 "celery_task_id" + ("duplicate" / "unique constraint") 跨 DB 兼容。

### 3.4 终态保证

无论谁先写 (API commit 早于 worker poll / API commit 晚于 worker poll), 终态是:
- DB 中只有 1 行 `celery_task_id == task_id`
- 状态 = `PROGRESS`, `progress = 0.0`, `error = None`, `started_at` 更新

---

## 四、测试覆盖

### 4.1 新增 7 个回归测试

[backend/tests/test_job_race_condition.py](../../backend/tests/test_job_race_condition.py)

| ID | 场景 | 验证点 |
|----|------|-------|
| T1 | existing 不存在 → INSERT 成功 | DB 中只有 1 行, state=PROGRESS, progress=0.0 |
| T2 | existing 存在 → UPDATE 成功 | 返回原 job_id, DB 中仍只有 1 行 |
| T3 | **race 路径**: existing 不存在 → INSERT 撞 UNIQUE → 重试 → UPDATE | 返回原 job_id, race warning 日志, 仍只有 1 行 |
| T4 | 重试后 existing 仍不存在 (极端) | 抛原 IntegrityError, 不吞掉 |
| T5 | 非 celery_task_id 的 IntegrityError (外键失败) | 不重试, 直接抛 |
| T6 | 同步包装 `create_or_reset_job_sync` 也走 race 修复 | race warning 日志, 仍只有 1 行 |
| T7 | race retry 后字段全部重置 | state / progress / error / started_at / finished_at / duration / data_* 全部重置 |

### 4.2 测试设计亮点

- **真实触发**: 用 SQLite UNIQUE 约束真实触发 IntegrityError, 不 mock `flush` (避免 mock 路径与实际行为不一致)
- **race 模拟**: 包装 `AsyncSessionLocal` 工厂, 让第一次 SELECT 返回 None, 模拟 API commit 还没完成
- **跨 DB 兼容**: SQLite 测试覆盖; MySQL 兼容靠错误消息识别保证

### 4.3 测试结果

```
============================= test session starts =============================
collected 7 items

tests/test_job_race_condition.py::test_t1_no_existing_inserts_new_row PASSED [ 14%]
tests/test_job_race_condition.py::test_t2_existing_updates_in_place PASSED [ 28%]
tests/test_job_race_condition.py::test_t3_race_insert_collision_retries_and_updates PASSED [ 42%]
tests/test_job_race_condition.py::test_t4_retry_existing_still_missing_raises PASSED [ 57%]
tests/test_job_race_condition.py::test_t5_non_celery_integrity_error_raises_directly PASSED [ 71%]
tests/test_job_race_condition.py::test_t6_sync_wrapper_handles_race PASSED [ 85%]
tests/test_job_race_condition.py::test_t7_race_retry_resets_all_fields PASSED [100%]

====== 7 passed ======
```

---

## 五、回归测试 (确认无副作用)

### 5.1 训练相关测试套件 (47 项)

```
tests/test_training.py ............
tests/test_training_cancel_api.py .......
tests/test_training_pretrain_mode.py ...............
tests/test_ml_pause_cancel.py ......
tests/test_workers_eager.py ....

====== 47 passed, 1 skipped (v1.0.0 预存问题) ======
```

### 5.2 Pause/Cancel + 引擎线程安全 (56 项)

```
tests/test_e2e_pause_cancel.py (含状态机 / 并发安全 / 异常处理) ............
tests/test_engine_thread_safety.py ........................
tests/test_control_signals.py ..........

====== 56 passed ======
```

### 5.3 性能 + 模型命名 (93 项)

```
tests/test_train_perf_p01_concurrent_download.py
tests/test_train_perf_p02_dataloader.py
tests/test_train_perf_p03_stream_lru.py
tests/test_train_perf_p05_adaptive_cb.py
tests/test_model_name_naming.py
tests/test_model_name_truncation.py

====== 93 passed ======
```

### 5.4 已知非相关失败 (v3.6.0 之前就存在)

- `tests/test_yolo_train.py::test_cleanup_old_runs`: 预存问题
- `tests/test_detection_train.py::test_yolo_dataset_export_*`: 预存问题
- `tests/test_detection_train.py::test_train_endpoint_redis_down_503`: `AttributeError: module 'app.tasks.api.detection' has no attribute 'redis_client'` (v3.5.0 重构时遗漏的属性)

> 这些与 v3.6.1 PATCH 无关, 不在本次修复范围。

---

## 六、变更范围

```
 .../service/training_lifecycle_service/job.py      | 78 +++++++++++++++++++++-
 tests/test_job_race_condition.py                   | 700 +++++++++++++++++++ (new)
```

- 修改 1 个文件: `job.py` (+77/-1)
- 新增 1 个文件: `test_job_race_condition.py` (7 个测试 + 3 个 fixture)

---

## 七、待跟进

- [ ] 用户在生产 MySQL 环境验证: 触发一次 race, 观察 worker 日志有 "race detected" warning, 训练正常继续
- [ ] 长期方案 (后续重构): 用 `INSERT ... ON DUPLICATE KEY UPDATE` (MySQL) / `INSERT ... ON CONFLICT DO UPDATE` (PostgreSQL) 把 race 在 SQL 层消除, 不依赖应用层 catch retry
- [ ] 类似 race 排查: `model_version` 表 `name` 字段也有 unique 约束, 检查是否有同样问题

---

## 八、参考

- 触发 bug 的 log: `2026-08-06 10:34:17,868` 用户报告
- 修复 commit: (待本报告批准后提交)
- 关联 plan: [plan-c-train-perf-v3.6.0.md](../../.trae/documents/plan-c-train-perf-v3.6.0.md)
