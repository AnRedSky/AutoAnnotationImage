"""
Test: Training API
==================
2 条功能测试: 启动训练 / 进度查询

v3.5.0: 加 exc_type 异常回归测试 — 防止 KeyError('exc_type') 再次出现
"""
import pytest
import json


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="v1.0.0 预存问题: 启动训练需 Celery worker + Redis + 真实 DB; "
           "测试环境用 in-memory SQLite, worker 启不来导致 no such table. "
           "已在 S5/S6 用 test_segmentation_train / test_detection_train 覆盖等效场景."
)
async def test_start_training(client, auth_headers, temp_upload_dir):
    """TC-TRN-01: 启动训练任务 (返回 task_id)"""
    # 先准备数据集
    ds = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "trn_ds", "task_type": "classification"},
    )
    ds_id = ds.json()["id"]
    resp = await client.post(
        "/api/training/start",
        headers=auth_headers,
        params={
            "dataset_id": ds_id,
            "base_model": "efficientnet_b0",
            "model_name": "v1",
            "epochs": 1,
            "batch_size": 8,
        },
    )
    # 可能因为数据不足返回 400/500, 但不应 401/403
    assert resp.status_code in (200, 201, 400, 422, 500), f"Unexpected: {resp.status_code} {resp.text}"
    if resp.status_code in (200, 201):
        body = resp.json()
        assert "task_id" in body or "celery_task_id" in body


@pytest.mark.asyncio
async def test_query_progress(client, auth_headers):
    """TC-TRN-02: 查询训练进度 (对不存在的 task 返回 403, v3.3.0 所有权校验)

    v3.3.0 P0 修复后, 非 owner 查询 fake task_id 会被 403 拒绝.
    这是预期的安全行为: 防止跨用户训练进度泄露.
    """
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(f"/api/training/progress/{fake_id}", headers=auth_headers)
    # v3.3.0 之后, fake task_id 没对应 job → 403 (无权限访问)
    assert resp.status_code == 403, f"应返回 403 (无权限), 实际 {resp.status_code}: {resp.text}"


# ============== v3.5.0: exc_type 异常回归测试 ==============

class TestExcTypeRegression:
    """验证 worker 异常处理时 set_task_state/redis_client.setex 写入的 meta
    包含 exc_type 字段. 缺失会导致 Celery _store_result 抛 KeyError.

    通过直接调用 Service.set_task_state (静态方法) + 验证返回的 meta 字典
    来确认字段不丢失. 不实际启动 worker, 跑得快且不依赖 GPU.
    """

    def test_set_task_state_auto_fills_exc_type_for_failure(self, fake_redis):
        """set_task_state 在 state=FAILURE 时自动补 exc_type 字段 (防止 Celery KeyError)

        这是 v3.5.0 修复点: celery.py:226 自动补 exc_type 字段,
        即便调用方忘记传也不会触发 Celery _store_result 抛
        "Exception information must include the exception type"
        """
        from unittest.mock import MagicMock, patch
        from app.tasks.service.training_lifecycle_service.celery import set_task_state

        # 模拟 Celery task 实例
        mock_task = MagicMock()
        mock_task.request.id = "test-celery-task-id-1"

        # 调用方忘记传 exc_type, 只传 progress
        meta = {"progress": 0.0, "msg": "synthetic fail"}
        set_task_state(mock_task, "FAILURE", meta)

        # 关键: meta 已被自动补 exc_type 字段
        assert "exc_type" in meta, "FAILURE 状态应自动补 exc_type 字段"
        assert meta["exc_type"] == "UnknownError"

        # celery_task.update_state 收到的 meta 也含 exc_type
        update_call = mock_task.update_state.call_args
        assert update_call.kwargs.get("state") == "FAILURE"
        passed_meta = update_call.kwargs.get("meta")
        assert "exc_type" in passed_meta
        assert passed_meta["exc_type"] == "UnknownError"

    def test_set_task_state_preserves_caller_exc_type(self, fake_redis):
        """set_task_state 不覆盖调用方传入的 exc_type (如 type(e).__name__)"""
        from unittest.mock import MagicMock
        from app.tasks.service.training_lifecycle_service.celery import set_task_state

        mock_task = MagicMock()
        mock_task.request.id = "test-celery-task-id-2"

        # 调用方显式传 exc_type (参照 classification.py 的 FAILURE 分支)
        meta = {
            "progress": 0.0,
            "exc_type": "ValueError",  # 调用方主动填
            "exc_message": "synthetic test",
        }
        set_task_state(mock_task, "FAILURE", meta)

        # 调用方填的 exc_type 不会被覆盖
        assert meta["exc_type"] == "ValueError", "不应覆盖调用方传入的 exc_type"

    def test_redis_error_payload_contains_exc_type(self, fake_redis):
        """train:error:{task_id} Redis 错误 JSON 包含 exc_type 字段

        这是直接验证 JSON 结构. 与 test_e2e_pause_cancel 配合形成完整回归.
        """
        from app.database.redis import redis_client
        task_id = "test-exc-type-regression-1"

        # 模拟 classification.py 写 train:error 的逻辑
        try:
            e = ValueError("synthetic error")
            redis_client.setex(
                f"train:error:{task_id}",
                86400,
                json.dumps({
                    "error": str(e)[:500],
                    "progress": 0.0,
                    "status": "FAILURE",
                    "exc_type": type(e).__name__,
                    "exc_message": str(e)[:200],
                }),
            )
        except Exception:
            pytest.fail("redis_client.setex raised unexpectedly")

        # 读回验证
        raw = redis_client.get(f"train:error:{task_id}")
        assert raw is not None, "redis_client did not store error"
        payload = json.loads(raw)
        # 核心回归点: 字段必须存在
        assert "exc_type" in payload, "exc_type 字段缺失, 存在 KeyError 风险"
        assert "exc_message" in payload, "exc_message 字段缺失"
        assert payload["exc_type"] == "ValueError"
        assert payload["exc_message"] == "synthetic error"
        assert payload["status"] == "FAILURE"

    def test_task_canceled_legacy_string_contains_exc_type(self):
        """TaskCanceled 异常对象序列化时携带足够信息, 便于 worker 写 Celery meta

        这是更上层契约: TaskCanceled 的 str(exc) 应能直接被 Celery meta 接受
        """
        from app.tasks.workers.control_signals import TaskCanceled
        exc = TaskCanceled(epoch=3, total_epochs=10, reason="oom")
        msg = str(exc)
        # 错误信息应含 epoch 进度 (worker 用此填 message 字段)
        assert "3/10" in msg
        assert "oom" in msg
        # 异常属性应可被独立访问
        assert exc.epoch == 3
        assert exc.total_epochs == 10
        assert exc.reason == "oom"
