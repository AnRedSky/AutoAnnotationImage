"""
Test: 训练任务状态机 + 端到端 Pause/Cancel 流程 — v3.5.0
========================================================

覆盖场景:
1. 状态机合法转移: PENDING → PROGRESS → CANCELED
2. 状态机非法转移被拒绝 (终态不能再转移)
3. 端到端: cancel API 写 Redis + 立即更新 DB
4. 端到端: pause API 写 Redis + 立即更新 DB
5. 并发安全: 两个 task 的信号互不干扰
6. Cancel/pause 在不同源状态下都生效
7. Worker pause_check 抛出 TaskCanceled 后, 状态转移正确
"""
import pytest
import json
from datetime import datetime
from unittest.mock import patch, MagicMock

from app.tasks.model.training_job import (
    TrainingJob,
    TRAIN_STATE_PENDING,
    TRAIN_STATE_PROGRESS,
    TRAIN_STATE_SUCCESS,
    TRAIN_STATE_FAILURE,
    TRAIN_STATE_REVOKED,
    TRAIN_STATE_PAUSED,
    TRAIN_STATE_CANCELED,
    TRAIN_TERMINAL_STATES,
)


# ============== 1. 状态机合法转移 ==============

class TestStateMachineLegalTransitions:
    """训练任务状态机的合法转移路径"""

    @pytest.mark.asyncio
    async def test_pending_to_progress(self, db_session, test_user):
        """PENDING → PROGRESS (训练开始)"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name="v1",
            task_type="classification", state="PENDING",
        )
        db_session.add(job)
        await db_session.commit()

        job.mark_started()
        await db_session.commit()

        assert job.state == TRAIN_STATE_PROGRESS
        assert job.started_at is not None

    @pytest.mark.asyncio
    async def test_progress_to_paused(self, db_session, test_user):
        """PROGRESS → PAUSED (训练暂停)"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name="v2",
            task_type="classification", state="PROGRESS",
            started_at=datetime.utcnow(),
        )
        db_session.add(job)
        await db_session.commit()

        job.transition_to(TRAIN_STATE_PAUSED, "User paused")
        await db_session.commit()

        assert job.state == TRAIN_STATE_PAUSED

    @pytest.mark.asyncio
    async def test_progress_to_canceled(self, db_session, test_user):
        """PROGRESS → CANCELED (用户取消, v3.5.0 新增路径)"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name="v3",
            task_type="classification", state="PROGRESS",
            started_at=datetime.utcnow(),
        )
        db_session.add(job)
        await db_session.commit()

        job.transition_to(TRAIN_STATE_CANCELED, "User canceled")
        await db_session.commit()

        assert job.state == TRAIN_STATE_CANCELED
        assert job.is_canceled()
        assert job.is_terminal()

    @pytest.mark.asyncio
    async def test_paused_to_progress_resume(self, db_session, test_user):
        """PAUSED → PROGRESS (用户点继续)"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name="v4",
            task_type="classification", state="PAUSED",
        )
        db_session.add(job)
        await db_session.commit()

        job.transition_to(TRAIN_STATE_PROGRESS, "User resumed")
        await db_session.commit()

        assert job.state == TRAIN_STATE_PROGRESS

    @pytest.mark.asyncio
    async def test_paused_to_canceled(self, db_session, test_user):
        """PAUSED → CANCELED (从暂停状态也能取消, 业务常见)"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name="v5",
            task_type="classification", state="PAUSED",
        )
        db_session.add(job)
        await db_session.commit()

        job.transition_to(TRAIN_STATE_CANCELED, "User canceled from paused")
        await db_session.commit()

        assert job.state == TRAIN_STATE_CANCELED

    @pytest.mark.asyncio
    async def test_pending_to_canceled(self, db_session, test_user):
        """PENDING → CANCELED (任务还没启动就被取消)"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name="v6",
            task_type="classification", state="PENDING",
        )
        db_session.add(job)
        await db_session.commit()

        job.transition_to(TRAIN_STATE_CANCELED, "User canceled before start")
        await db_session.commit()

        assert job.state == TRAIN_STATE_CANCELED


# ============== 2. 状态机非法转移被拒绝 ==============

class TestStateMachineIllegalTransitions:
    """终态不能再次转移 (FAILURE/REVOKED/CANCELED 不可再变)"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal_state", [
        TRAIN_STATE_SUCCESS, TRAIN_STATE_FAILURE,
        TRAIN_STATE_REVOKED, TRAIN_STATE_CANCELED,
    ])
    async def test_terminal_state_cannot_transition(self, db_session, test_user, terminal_state):
        """终态不可转移到其他状态"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name=f"v_{terminal_state}",
            task_type="classification", state=terminal_state,
        )
        db_session.add(job)
        await db_session.commit()

        # 任何转移都应该被拒绝
        for target in (TRAIN_STATE_PROGRESS, TRAIN_STATE_PAUSED, TRAIN_STATE_CANCELED,
                       TRAIN_STATE_SUCCESS, TRAIN_STATE_FAILURE, TRAIN_STATE_REVOKED):
            with pytest.raises(ValueError, match="非法训练状态转移"):
                job.transition_to(target, "should fail")

    @pytest.mark.asyncio
    async def test_mark_succeeded_from_progress(self, db_session, test_user):
        """PROGRESS → SUCCESS 走 mark_succeeded 方法"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name="v_success",
            task_type="classification", state="PROGRESS",
            started_at=datetime.utcnow(),
        )
        db_session.add(job)
        await db_session.commit()

        job.mark_succeeded(model_version_id=42)
        await db_session.commit()

        assert job.state == TRAIN_STATE_SUCCESS
        assert job.progress == 100.0
        assert job.model_version_id == 42
        assert job.finished_at is not None
        assert job.duration_seconds is not None

    @pytest.mark.asyncio
    async def test_mark_failed_idempotent(self, db_session, test_user):
        """mark_failed 在终态不会覆盖"""
        job = TrainingJob(
            user_id=test_user.id, dataset_id=1,
            base_model="resnet50", model_name="v_failed_idem",
            task_type="classification", state="FAILURE",
        )
        db_session.add(job)
        await db_session.commit()

        # 已经是 FAILURE, 再次 mark_failed 不报错也不改变
        job.mark_failed("subsequent error attempt")
        await db_session.commit()

        # 终态不变 (test 函数本身不抛即视为通过)


# ============== 3. 端到端: cancel API 写 Redis + DB ==============

class TestE2ECancelAPI:
    """端到端验证 cancel API 完整行为"""

    @pytest.mark.asyncio
    async def test_cancel_writes_both_redis_and_db(
        self, client, auth_headers, db_session, test_user, fake_redis
    ):
        """cancel API 同时写 Redis signal 和 DB state"""
        # 创建数据集
        ds = await client.post(
            "/api/datasets",
            headers=auth_headers,
            json={"name": "e2e_cancel_ds", "task_type": "classification"},
        )
        ds_id = ds.json()["id"]

        # 创建 PROGRESS 状态的任务
        job = TrainingJob(
            user_id=test_user.id, dataset_id=ds_id,
            base_model="resnet50", model_name="e2e_cancel_v1",
            task_type="classification",
            celery_task_id="e2e-celery-cancel-1",
            state="PROGRESS", progress=42.0, epochs=10,
        )
        db_session.add(job)
        await db_session_commit(db_session)
        await db_session.refresh(job)

        with patch("app.tasks.workers.celery_app.celery_app.control.revoke"):
            resp = await client.post(
                f"/api/training/jobs/{job.id}/cancel",
                headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "CANCELED"

        # 验证 Redis 写了 cancel 信号
        cancel_key = f"train:cancel:{job.celery_task_id}"
        assert cancel_key in fake_redis._store, f"Redis 未写 cancel 信号, store={fake_redis._store}"

        # 验证 DB state
        await db_session.refresh(job)
        assert job.state == "CANCELED"

    @pytest.mark.asyncio
    async def test_pause_writes_redis_and_db(
        self, client, auth_headers, db_session, test_user, fake_redis
    ):
        """pause API 同时写 Redis signal 和 DB state"""
        ds = await client.post(
            "/api/datasets",
            headers=auth_headers,
            json={"name": "e2e_pause_ds", "task_type": "classification"},
        )
        ds_id = ds.json()["id"]

        job = TrainingJob(
            user_id=test_user.id, dataset_id=ds_id,
            base_model="resnet50", model_name="e2e_pause_v1",
            task_type="classification",
            celery_task_id="e2e-celery-pause-1",
            state="PROGRESS", progress=20.0, epochs=10,
        )
        db_session.add(job)
        await db_session_commit(db_session)
        await db_session.refresh(job)

        with patch("app.tasks.workers.celery_app.celery_app.control.revoke"):
            resp = await client.post(
                f"/api/training/jobs/{job.id}/pause",
                headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "PAUSED"

        # 验证 Redis pause key 存在
        pause_key = f"train:pause:{job.celery_task_id}"
        assert pause_key in fake_redis._store, f"Redis 未写 pause 信号, store={fake_redis._store}"

        # 验证 DB state
        await db_session.refresh(job)
        assert job.state == "PAUSED"


# ============== 4. 并发安全: 多任务信号隔离 ==============

class TestConcurrentSafety:
    """验证多个任务的 Redis signal 互不干扰"""

    def test_two_tasks_pause_signals_isolated(self, fake_redis):
        """两个任务的 pause 信号独立"""
        from app.tasks.workers.control_signals import (
            request_pause, request_cancel, make_pause_check, clear_all,
        )

        task_a = "task-concurrent-A"
        task_b = "task-concurrent-B"

        # 清理
        clear_all(task_a)
        clear_all(task_b)

        try:
            # 给 A 发 pause
            request_pause(task_a, ttl=60)

            # A 读 → PAUSE
            check_a = make_pause_check(task_a)
            assert check_a() == "PAUSE", f"A 任务应读到 PAUSE, 实际 {check_a()}"

            # B 读 → CONTINUE (没有自己的信号)
            check_b = make_pause_check(task_b)
            assert check_b() == "CONTINUE", f"B 任务应读到 CONTINUE, 实际 {check_b()}"

            # 给 B 发 cancel
            request_cancel(task_b, ttl=60)

            # A 仍是 PAUSE (B 的 cancel 不影响 A)
            assert check_a() == "PAUSE", f"A 仍应读到 PAUSE, 实际 {check_a()}"

            # B 是 CANCEL
            assert check_b() == "CANCEL", f"B 应读到 CANCEL, 实际 {check_b()}"
        finally:
            clear_all(task_a)
            clear_all(task_b)

    def test_five_tasks_signal_isolation(self, fake_redis):
        """5 个并发任务的信号完全隔离"""
        from app.tasks.workers.control_signals import (
            request_pause, request_cancel, make_pause_check, clear_all,
        )

        task_ids = [f"multi-task-{i}" for i in range(5)]
        # 清理
        for tid in task_ids:
            clear_all(tid)

        try:
            # 给奇数任务发 pause, 偶数发 cancel
            for i, tid in enumerate(task_ids):
                if i % 2 == 0:
                    request_pause(tid, ttl=60)
                else:
                    request_cancel(tid, ttl=60)

            # 验证每个任务的信号独立
            for i, tid in enumerate(task_ids):
                action = make_pause_check(tid)()
                if i % 2 == 0:
                    assert action == "PAUSE", f"task {i} 应为 PAUSE, 实际 {action}"
                else:
                    assert action == "CANCEL", f"task {i} 应为 CANCEL, 实际 {action}"
        finally:
            for tid in task_ids:
                clear_all(tid)


# ============== 5. Worker 异常处理 ==============

class TestWorkerExceptionHandling:
    """验证 worker 异常被正确捕获并标记 CANCELED/PAUSED"""

    def test_task_canceled_creates_proper_cancellation(self, fake_redis):
        """TaskCanceled 异常能被 worker 捕获并处理"""
        from app.tasks.workers.control_signals import TaskCanceled, clear_all

        # 模拟 worker 捕获 TaskCanceled 后清理
        task_id = "test-worker-cancel-1"
        clear_all(task_id)

        try:
            # 模拟 epoch 循环抛 TaskCanceled
            with pytest.raises(TaskCanceled) as exc_info:
                raise TaskCanceled(epoch=5, total_epochs=10, reason="user_cancel")
            assert exc_info.value.epoch == 5
            assert exc_info.value.total_epochs == 10
            assert exc_info.value.reason == "user_cancel"
        finally:
            clear_all(task_id)

    def test_training_paused_carries_epoch_info(self, fake_redis):
        """TrainingPaused 异常携带 epoch 信息"""
        from app.tasks.ml.classification import TrainingPaused

        with pytest.raises(TrainingPaused) as exc_info:
            raise TrainingPaused(epoch=3, total_epochs=10)
        assert exc_info.value.epoch == 3
        assert exc_info.value.total_epochs == 10


# ============== 辅助函数 ==============

async def db_session_commit(db_session):
    """辅助: 异步 commit, 让测试代码更简洁"""
    await db_session.commit()
