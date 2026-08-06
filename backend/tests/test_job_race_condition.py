"""
v3.6.1 PATCH — TrainingJob create_or_reset_job race condition 兜底
==================================================================

**Bug 场景** (用户报告 2026-08-06):
- 用户先点"暂停"再点"继续训练"
- API `/api/training/jobs/{id}/start?mode=resume` 处理流程:
  1) `task.delay()` 投递 Celery 任务 (Celery 自动生成 task_id, 如 `a8a7000d-...`)
  2) `db.commit()` UPDATE 旧 job 行的 `celery_task_id = task.id`
- Worker 端 `train_model_task` 启动, 调 `create_or_reset_job_sync(task_id=a8a7000d-...)`:
  - 第一次 SELECT 时, API commit 可能尚未完成 → 找不到 existing
  - 走 INSERT 路径
  - **Race**: API 的 commit 在 worker 的 INSERT 之前完成
  - INSERT 撞 `ix_training_jobs_celery_task_id` unique key → IntegrityError
  - worker 任务挂掉, 训练任务失败

**修复** (v3.6.1 PATCH):
- `create_or_reset_job` 在 INSERT 失败时 catch IntegrityError
- 跨 DB 错误消息识别:
  - MySQL:    (1062, "Duplicate entry 'xxx' for key 'ix_training_jobs_celery_task_id'")
  - SQLite:   UNIQUE constraint failed: training_jobs.celery_task_id
  - 两者都含 "celery_task_id" + ("duplicate" / "unique constraint") → 视为 race
- 回滚当前 INSERT, 重新查询 (此时 API 的 commit 一定完成)
- 走 UPDATE 路径, 复用现有 reset 逻辑 (inlined 避免双层嵌套)

**测试覆盖** (7 个):
- T1: 正常路径 (existing 不存在 → INSERT 成功, 单行)
- T2: 正常路径 (existing 存在 → UPDATE 成功, 仍是单行)
- T3: **race 路径** (existing 不存在 → INSERT 撞 UNIQUE → 重试 → UPDATE 成功, 仍是单行)
- T4: 重试后 existing 仍不存在 (极端情况) → 抛原 IntegrityError
- T5: 非 celery_task_id 的 IntegrityError (外键失败) → 不重试, 直接抛
- T6: 同步包装 `create_or_reset_job_sync` 也走 race 修复
- T7: race retry 后字段全部重置 (state/progress/error/started_at/finished_at/duration/data_*)

**mock 策略** (v3.6.1 PATCH 简版):
- 真实 INSERT 走 SQLite UNIQUE 约束触发 IntegrityError (无需 mock flush)
- 用 `_RaceSelectWrapper` 等包装类拦截 `AsyncSessionLocal` 工厂
  - 第一次 SELECT 返回 None (模拟 API commit 还没完成的 race window)
  - 第二次 SELECT 返回真实数据 (race 结束)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from app.tasks.service.training_lifecycle_service.job import (
    create_or_reset_job,
    create_or_reset_job_sync,
)


# ============== 共享 helper ==============


class _FakeScalarResult:
    """模拟 SELECT 返回空 (scalar_one_or_none -> None) 的场景"""
    def scalar_one_or_none(self):
        return None


# ============== T1: 正常路径 - existing 不存在 → INSERT 成功 ==============


@pytest.mark.asyncio
async def test_t1_no_existing_inserts_new_row(db_session, celery_eager):
    """场景: DB 中无该 task_id 的行, create_or_reset_job 应该 INSERT 新行"""
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob
    from sqlalchemy import select

    task_id = uuid.uuid4().hex
    started_at = datetime.utcnow()

    job_id = await create_or_reset_job(
        task_id=task_id,
        user_id=18,
        dataset_id=45,
        base_model="resnet50",
        model_name="resnet50_v1",
        task_type="classification",
        epochs=20,
        batch_size=32,
        learning_rate=1e-4,
        started_at=started_at,
    )

    assert job_id is not None
    assert isinstance(job_id, int)

    # 验证 DB 中只有 1 行
    async with AsyncSessionLocal() as sdb:
        rows = (await sdb.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalars().all()
        assert len(rows) == 1, f"应只有 1 行, 实际 {len(rows)}"
        row = rows[0]
        assert row.state == "PROGRESS"
        assert row.progress == 0.0
        assert row.user_id == 18
        assert row.dataset_id == 45


# ============== T2: 正常路径 - existing 存在 → UPDATE 成功 ==============


@pytest.mark.asyncio
async def test_t2_existing_updates_in_place(db_session, celery_eager):
    """场景: DB 中已有该 task_id 的 PENDING 行, create_or_reset_job 应该 UPDATE 现有行 (不 INSERT)"""
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob
    from sqlalchemy import select

    task_id = uuid.uuid4().hex
    started_at = datetime.utcnow()

    # 预创建 PENDING 行 (模拟 API 端 start_training 流程)
    async with AsyncSessionLocal() as sdb:
        existing = TrainingJob(
            celery_task_id=task_id,
            user_id=18,
            dataset_id=45,
            base_model="resnet50",
            model_name="resnet50_v1",
            task_type="classification",
            epochs=20,
            batch_size=32,
            learning_rate=1e-4,
            state="PENDING",
            progress=0.0,
            message="等待 worker 启动...",
            started_at=None,
        )
        sdb.add(existing)
        await sdb.commit()
        await sdb.refresh(existing)
        original_id = existing.id

    # 调 create_or_reset_job
    job_id = await create_or_reset_job(
        task_id=task_id,
        user_id=18,
        dataset_id=45,
        base_model="resnet50",
        model_name="resnet50_v1",
        task_type="classification",
        epochs=20,
        batch_size=32,
        learning_rate=1e-4,
        started_at=started_at,
    )

    assert job_id == original_id, f"应返回原 job_id={original_id}, 实际 {job_id}"

    # 验证仍是同一行 (没 INSERT 新行)
    async with AsyncSessionLocal() as sdb:
        rows = (await sdb.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalars().all()
        assert len(rows) == 1, f"应仍只有 1 行, 实际 {len(rows)}"
        row = rows[0]
        assert row.state == "PROGRESS"
        assert row.progress == 0.0
        assert row.error is None
        assert row.started_at is not None


# ============== T3: RACE 路径 - 真实 SQLite UNIQUE 撞 → 重试 → UPDATE 成功 ==============


@pytest_asyncio.fixture
async def race_session_factory(db_session, monkeypatch):
    """模拟 race window 的 AsyncSessionLocal 工厂

    模拟行为:
    - 第一次 SELECT TrainingJob WHERE celery_task_id=X: 返回 None (模拟 API commit 还没完成)
    - 但 DB 中确实有该 task_id 的行 (INSERT 会撞真实 UNIQUE 约束)
    - 第二次 SELECT (在 retry 时): 返回真实数据

    实现: 拦截 AsyncSessionLocal 工厂, 在新 session 上包装 execute
    """
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    race_state = {"n_selects": 0}

    real_session_local = AsyncSessionLocal

    class RaceAwareSession:
        """对 race 测试场景: 第一次 SELECT 返回 None, 之后正常"""

        def __init__(self, *args, **kwargs):
            # 创建一个真实 session 作为底层
            self._real_session = real_session_local(*args, **kwargs)
            self._race_state = race_state

        async def __aenter__(self):
            await self._real_session.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self._real_session.__aexit__(*args)

        async def execute(self, stmt, *args, **kwargs):
            # 检查是否是 celery_task_id SELECT
            try:
                stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            except Exception:
                stmt_str = str(stmt)
            if "celery_task_id" in stmt_str and "training_jobs" in stmt_str.lower():
                self._race_state["n_selects"] += 1
                if self._race_state["n_selects"] == 1:
                    # 第一次: 模拟 race window, 返回 None
                    return _FakeScalarResult()
            return await self._real_session.execute(stmt, *args, **kwargs)

        def __getattr__(self, name):
            # 其他方法 (commit, rollback, add, refresh, get 等) 透传
            return getattr(self._real_session, name)

    def factory(*args, **kwargs):
        return RaceAwareSession(*args, **kwargs)

    # patch app.database.AsyncSessionLocal (create_or_reset_job 内部 import 这个)
    monkeypatch.setattr("app.database.AsyncSessionLocal", factory)

    return race_state


@pytest.mark.asyncio
async def test_t3_race_insert_collision_retries_and_updates(
    db_session, celery_eager, race_session_factory, caplog
):
    """场景: API 端先 UPDATE 旧 job 行 celery_task_id 为新 task_id (已 commit),
    worker 端第一次 SELECT 看不到, 走 INSERT 撞真实 SQLite UNIQUE 约束,
    应自动 catch IntegrityError, 重试 SELECT 看到, 走 UPDATE 路径.

    这是 v3.6.1 PATCH 修复的核心场景.
    """
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob
    from sqlalchemy import select

    task_id = uuid.uuid4().hex
    started_at = datetime.utcnow()

    # 1) 预创建 PENDING 行 (模拟 API 端 start_training 流程)
    #    用真实 AsyncSessionLocal (不通过 race_session_factory) 创建
    async with AsyncSessionLocal() as sdb:
        existing = TrainingJob(
            celery_task_id=task_id,
            user_id=18,
            dataset_id=45,
            base_model="resnet50",
            model_name="resnet50_v1",
            task_type="classification",
            epochs=20,
            batch_size=32,
            learning_rate=1e-4,
            state="PENDING",
            progress=0.0,
            message="等待 worker 启动...",
            started_at=None,
        )
        sdb.add(existing)
        await sdb.commit()
        await sdb.refresh(existing)
        original_id = existing.id

    caplog.set_level(logging.WARNING)

    # 2) 调 create_or_reset_job
    #    第一次 SELECT 返回 None (race window)
    #    → 走 INSERT 路径
    #    → SQLite UNIQUE 约束触发 IntegrityError(1062)
    #    → catch → rollback → 重试 SELECT
    #    → 第二次 SELECT 返回 existing (race 结束)
    #    → 走 UPDATE 路径, 复用 reset 逻辑
    job_id = await create_or_reset_job(
        task_id=task_id,
        user_id=18,
        dataset_id=45,
        base_model="resnet50",
        model_name="resnet50_v1",
        task_type="classification",
        epochs=20,
        batch_size=32,
        learning_rate=1e-4,
        started_at=started_at,
    )

    # 3) 验证:
    assert job_id == original_id, (
        f"race retry 应返回原 job_id={original_id}, 实际 {job_id}"
    )

    # DB 中仍只有 1 行 (没多出 INSERT 的行)
    async with AsyncSessionLocal() as sdb:
        rows = (await sdb.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalars().all()
        assert len(rows) == 1, f"race retry 后应仍只有 1 行, 实际 {len(rows)}"
        row = rows[0]
        assert row.state == "PROGRESS"
        assert row.progress == 0.0
        assert row.error is None

    # race warning 日志必须出现
    race_logs = [r for r in caplog.records if "race detected" in r.message]
    assert len(race_logs) >= 1, (
        f"应至少有 1 条 race warning 日志, 实际 {len(race_logs)} 条. "
        f"全部 race job 日志: {[r.message for r in caplog.records if 'race' in r.message.lower()]}"
    )


# ============== T4: 重试后 existing 仍不存在 (极端情况) → 抛原 IntegrityError ==============


@pytest_asyncio.fixture
async def always_empty_session_factory(db_session, monkeypatch):
    """模拟 race window 永远不结束的 AsyncSessionLocal 工厂

    用于测试极端情况: 两次 SELECT 都返回 None (race retry 找不到 existing)
    """
    from app.database import AsyncSessionLocal

    real_session_local = AsyncSessionLocal

    class AlwaysEmptySession:
        def __init__(self, *args, **kwargs):
            self._real_session = real_session_local(*args, **kwargs)

        async def __aenter__(self):
            await self._real_session.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self._real_session.__aexit__(*args)

        async def execute(self, stmt, *args, **kwargs):
            try:
                stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            except Exception:
                stmt_str = str(stmt)
            if "celery_task_id" in stmt_str and "training_jobs" in stmt_str.lower():
                return _FakeScalarResult()
            return await self._real_session.execute(stmt, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real_session, name)

    def factory(*args, **kwargs):
        return AlwaysEmptySession(*args, **kwargs)

    monkeypatch.setattr("app.database.AsyncSessionLocal", factory)
    return


@pytest.mark.asyncio
async def test_t4_retry_existing_still_missing_raises(
    db_session, celery_eager, always_empty_session_factory
):
    """场景: race 时 INSERT 撞 1062, 重试时 existing 仍不存在 (极端情况, 行被删)
    → 抛原 IntegrityError, 不吞掉错误
    """
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob

    task_id = uuid.uuid4().hex
    started_at = datetime.utcnow()

    # 预创建 PENDING 行
    async with AsyncSessionLocal() as sdb:
        existing = TrainingJob(
            celery_task_id=task_id,
            user_id=18,
            dataset_id=45,
            base_model="resnet50",
            model_name="resnet50_v1",
            task_type="classification",
            epochs=20,
            batch_size=32,
            learning_rate=1e-4,
            state="PENDING",
            progress=0.0,
            message="等待 worker 启动...",
            started_at=None,
        )
        sdb.add(existing)
        await sdb.commit()

    # 调 create_or_reset_job, 因为 race SELECT 永远返回 None
    # 第一次 INSERT 撞 SQLite UNIQUE → 重试 SELECT 仍 None → 抛 IntegrityError
    with pytest.raises(IntegrityError):
        await create_or_reset_job(
            task_id=task_id,
            user_id=18,
            dataset_id=45,
            base_model="resnet50",
            model_name="resnet50_v1",
            task_type="classification",
            epochs=20,
            batch_size=32,
            learning_rate=1e-4,
            started_at=started_at,
        )


# ============== T5: 非 celery_task_id 的 IntegrityError → 不重试, 直接抛 ==============


@pytest_asyncio.fixture
async def non_celery_integrity_factory(db_session, monkeypatch):
    """模拟 INSERT 撞非 celery_task_id 的 IntegrityError (如外键失败)

    第一次 SELECT 返回 None, INSERT 抛 1452 FOREIGN KEY 错误
    """
    from app.database import AsyncSessionLocal
    from sqlalchemy.exc import IntegrityError

    real_session_local = AsyncSessionLocal

    class NonCeleryIntegritySession:
        def __init__(self, *args, **kwargs):
            self._real_session = real_session_local(*args, **kwargs)
            self._n_selects = 0
            self._n_inserts = 0

        async def __aenter__(self):
            await self._real_session.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self._real_session.__aexit__(*args)

        async def execute(self, stmt, *args, **kwargs):
            try:
                stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            except Exception:
                stmt_str = str(stmt)
            if "celery_task_id" in stmt_str and "training_jobs" in stmt_str.lower():
                self._n_selects += 1
                if self._n_selects == 1:
                    return _FakeScalarResult()
            return await self._real_session.execute(stmt, *args, **kwargs)

        async def commit(self):
            # 模拟 INSERT 撞外键 (1452, 不是 1062)
            self._n_inserts += 1
            if self._n_inserts == 1:
                raise IntegrityError(
                    "INSERT INTO training_jobs ...",
                    params={},
                    orig=Exception(
                        "(pymysql.err.IntegrityError) (1452, "
                        "\"Cannot add or update a child row: "
                        "FOREIGN KEY constraint fails\")"
                    ),
                )
            return await self._real_session.commit()

        def __getattr__(self, name):
            return getattr(self._real_session, name)

    def factory(*args, **kwargs):
        return NonCeleryIntegritySession(*args, **kwargs)

    monkeypatch.setattr("app.database.AsyncSessionLocal", factory)
    return


@pytest.mark.asyncio
async def test_t5_non_celery_integrity_error_raises_directly(
    db_session, celery_eager, non_celery_integrity_factory
):
    """场景: INSERT 撞的是非 celery_task_id 的 unique key (如外键约束失败)
    → 不属于 race, 不应重试, 直接抛 IntegrityError
    """
    task_id = uuid.uuid4().hex
    started_at = datetime.utcnow()

    with pytest.raises(IntegrityError) as exc_info:
        await create_or_reset_job(
            task_id=task_id,
            user_id=18,
            dataset_id=45,
            base_model="resnet50",
            model_name="resnet50_v1",
            task_type="classification",
            epochs=20,
            batch_size=32,
            learning_rate=1e-4,
            started_at=started_at,
        )

    # 验证错误是原始的 1452, 不是 race 重试后的
    err_str = str(exc_info.value)
    assert "1452" in err_str or "FOREIGN KEY" in err_str.upper(), (
        f"应是非 celery_task_id 的 1452 错误, 实际: {err_str}"
    )


# ============== T6: 同步包装 create_or_reset_job_sync 也走 race 修复 ==============


def test_t6_sync_wrapper_handles_race(db_session, celery_eager, caplog, monkeypatch):
    """场景: create_or_reset_job_sync (worker 实际调用) 也能处理 race condition

    实现:
    1) 在 sync 测试里用独立 event loop 跑 setup (因为 pytest-asyncio 的 loop 不能嵌套)
    2) monkeypatch app.database.AsyncSessionLocal, 模拟 race window
    3) create_or_reset_job_sync → _run_async → create_or_reset_job,
       内部 from app.database import AsyncSessionLocal 拿到的就是 mock 工厂
    """
    from app.database import AsyncSessionLocal as _RealAsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob
    from sqlalchemy import select
    import asyncio

    # 准备 race-aware session factory (与 T3 同样的封装逻辑)
    class _FakeScalarResult:
        def scalar_one_or_none(self):
            return None

    class RaceAwareSession:
        def __init__(self, *args, **kwargs):
            self._real = _RealAsyncSessionLocal(*args, **kwargs)
            self._n_selects = 0

        async def __aenter__(self):
            await self._real.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self._real.__aexit__(*args)

        async def execute(self, stmt, *args, **kwargs):
            try:
                stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            except Exception:
                stmt_str = str(stmt)
            if "celery_task_id" in stmt_str and "training_jobs" in stmt_str.lower():
                self._n_selects += 1
                if self._n_selects == 1:
                    return _FakeScalarResult()
            return await self._real.execute(stmt, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def factory(*args, **kwargs):
        return RaceAwareSession(*args, **kwargs)

    # patch app.database.AsyncSessionLocal
    monkeypatch.setattr("app.database.AsyncSessionLocal", factory)

    task_id = uuid.uuid4().hex
    started_at = datetime.utcnow()

    # 用独立 event loop 跑 (避免与 pytest-asyncio 的 loop 冲突)
    loop = asyncio.new_event_loop()
    try:
        async def _precreate():
            # 注意: 这里 race-aware 包装已生效, 第一次 SELECT 会返回 None.
            # 但我们用真实的 AsyncSessionLocal 创建, 跳过 race-aware.
            # → 在 race-aware 模式下创建: 第一次 SELECT 返回 None → INSERT
            #    → 该 INSERT 会撞真实的 UNIQUE 约束吗? 不会, 因为这是预创建
            #    → 第一次 SELECT 是 race-aware 看不到, INSERT 又没撞 (因为此时
            #      DB 中没数据), 就会成功创建. 后续 SELECT 正常返回数据.
            # 这里我们需要"预创建"绕开 race-aware → 用 _RealAsyncSessionLocal
            async with _RealAsyncSessionLocal() as sdb:
                existing = TrainingJob(
                    celery_task_id=task_id,
                    user_id=18,
                    dataset_id=45,
                    base_model="resnet50",
                    model_name="resnet50_v1",
                    task_type="classification",
                    epochs=20,
                    batch_size=32,
                    learning_rate=1e-4,
                    state="PENDING",
                    progress=0.0,
                    message="等待 worker 启动...",
                    started_at=None,
                )
                sdb.add(existing)
                await sdb.commit()
                await sdb.refresh(existing)
                return existing.id
        original_id = loop.run_until_complete(_precreate())

        caplog.set_level(logging.WARNING)

        # 调同步包装 (worker 实际调用路径)
        # 内部会调 _run_async → create_or_reset_job, 走 race-aware 工厂
        # 第一次 SELECT 返回 None (race window) → INSERT → 撞真实 UNIQUE
        # → catch → 重试 SELECT → 返回 existing → UPDATE
        job_id = create_or_reset_job_sync(
            task_id=task_id,
            user_id=18,
            dataset_id=45,
            base_model="resnet50",
            model_name="resnet50_v1",
            task_type="classification",
            epochs=20,
            batch_size=32,
            learning_rate=1e-4,
            started_at=started_at,
        )

        assert job_id == original_id, (
            f"sync wrapper race retry 应返回原 job_id={original_id}, 实际 {job_id}"
        )

        # 验证 race warning 日志
        race_logs = [r for r in caplog.records if "race detected" in r.message]
        assert len(race_logs) >= 1, (
            f"应至少有 1 条 race warning 日志, 实际 {len(race_logs)} 条. "
            f"全部 job 日志: {[r.message for r in caplog.records if 'job' in r.message.lower()]}"
        )

        # DB 中仍只有 1 行
        async def _check():
            async with _RealAsyncSessionLocal() as sdb:
                rows = (await sdb.execute(
                    select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
                )).scalars().all()
                return len(rows)
        row_count = loop.run_until_complete(_check())
        assert row_count == 1, f"应仍只有 1 行, 实际 {row_count}"
    finally:
        loop.close()


# ============== T7: race retry 后字段正确重置 ==============


@pytest.mark.asyncio
async def test_t7_race_retry_resets_all_fields(
    db_session, celery_eager, race_session_factory, caplog
):
    """场景: race retry 走 UPDATE 路径后, 字段应完整重置
    - state = PROGRESS
    - progress = 0.0
    - error = None
    - started_at = 新时间
    - finished_at = None
    - duration_seconds = None
    """
    from app.database import AsyncSessionLocal
    from app.tasks.model.training_job import TrainingJob
    from sqlalchemy import select

    task_id = uuid.uuid4().hex
    started_at = datetime.utcnow()

    # 预创建带各种残留字段的 PENDING 行 (模拟上次失败的 job)
    async with AsyncSessionLocal() as sdb:
        existing = TrainingJob(
            celery_task_id=task_id,
            user_id=18,
            dataset_id=45,
            base_model="resnet50",
            model_name="resnet50_v1",
            task_type="classification",
            epochs=20,
            batch_size=32,
            learning_rate=1e-4,
            state="PENDING",
            progress=0.5,  # 残留进度
            message="等待 worker 启动...",
            error="上次失败的错误信息",  # 残留错误
            started_at=None,
            finished_at=datetime(2025, 1, 1, 0, 0, 0),  # 残留 finished
            duration_seconds=999,  # 残留 duration
        )
        sdb.add(existing)
        await sdb.commit()
        await sdb.refresh(existing)
        original_id = existing.id

    caplog.set_level(logging.WARNING)

    job_id = await create_or_reset_job(
        task_id=task_id,
        user_id=18,
        dataset_id=45,
        base_model="resnet50",
        model_name="resnet50_v1",
        task_type="classification",
        epochs=20,
        batch_size=32,
        learning_rate=1e-4,
        started_at=started_at,
    )

    assert job_id == original_id

    # 验证字段全部重置
    async with AsyncSessionLocal() as sdb:
        row = (await sdb.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalar_one()
        assert row.state == "PROGRESS"
        assert row.progress == 0.0
        assert row.error is None
        assert row.started_at is not None
        assert row.finished_at is None
        assert row.duration_seconds is None
        # 数据集统计也重置
        assert row.data_total is None
        assert row.data_train is None
        assert row.data_val is None
        assert row.num_classes is None
        assert row.class_names is None
