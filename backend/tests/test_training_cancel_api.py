"""
Test: 训练任务取消/暂停 API — v3.5.0
=====================================

覆盖:
1. cancel API 改用 control_signals.request_cancel (而非 Celery revoke terminate=True)
2. cancel API 立即写 DB CANCELED (不等 worker 退出)
3. cancel API 允许从 PENDING/PROGRESS/PAUSED 取消
4. cancel API 终态任务直接返回 success=False
5. 权限校验: 非 owner 不可取消
"""
import pytest
from unittest.mock import patch, MagicMock

from app.tasks.model.training_job import TrainingJob


@pytest.mark.asyncio
async def test_cancel_endpoint_writes_redis_signal(
    client, auth_headers, db_session, test_user, temp_upload_dir
):
    """cancel API 调用 control_signals.request_cancel 写 Redis 信号"""
    # 创建数据集
    ds_resp = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "cancel_test_ds", "task_type": "classification"},
    )
    assert ds_resp.status_code in (200, 201), ds_resp.text
    ds_id = ds_resp.json()["id"]

    # 创建一个 PROGRESS 状态的 TrainingJob (模拟正在跑)
    job = TrainingJob(
        user_id=test_user.id,
        dataset_id=ds_id,
        base_model="efficientnet_b0",
        model_name="cancel_test_v1",
        task_type="classification",
        celery_task_id="test-celery-task-cancel-1",
        state="PROGRESS",
        progress=42.0,
        epochs=10,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Mock control_signals.request_cancel, 验证 API 真的调用了
    with patch("app.tasks.workers.control_signals.request_cancel") as mock_cancel:
        mock_cancel.return_value = True
        # 也 mock Celery revoke 避免 broker 调用
        with patch("app.tasks.workers.celery_app.celery_app.control.revoke") as mock_revoke:
            resp = await client.post(
                f"/api/training/jobs/{job.id}/cancel",
                headers=auth_headers,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # API 应返回 success=True + state=CANCELED
            assert body.get("success") is True
            assert body.get("state") == "CANCELED"

            # 关键: control_signals.request_cancel 被调用过
            assert mock_cancel.called
            # revoke 用 terminate=False 调用
            assert mock_revoke.called
            kwargs = mock_revoke.call_args.kwargs
            assert kwargs.get("terminate") is False

    # 验证 DB 已更新为 CANCELED
    await db_session.refresh(job)
    assert job.state == "CANCELED"


@pytest.mark.asyncio
async def test_cancel_terminal_state_returns_failure(
    client, auth_headers, db_session, test_user
):
    """终态任务 (SUCCESS/FAILURE/REVOKED/CANCELED) 取消返回 success=False"""
    for terminal in ["SUCCESS", "FAILURE", "REVOKED", "CANCELED"]:
        job = TrainingJob(
            user_id=test_user.id,
            dataset_id=1,
            base_model="efficientnet_b0",
            model_name=f"cancel_terminal_{terminal}",
            task_type="classification",
            celery_task_id=f"test-celery-{terminal}",
            state=terminal,
            progress=100.0 if terminal == "SUCCESS" else 50.0,
            epochs=10,
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        resp = await client.post(
            f"/api/training/jobs/{job.id}/cancel",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("success") is False
        assert terminal in body.get("message", "")
        assert body.get("state") == terminal


@pytest.mark.asyncio
async def test_cancel_from_paused_state(
    client, auth_headers, db_session, test_user
):
    """从 PAUSED 状态也能取消 (业务场景: 暂停后用户改主意)"""
    job = TrainingJob(
        user_id=test_user.id,
        dataset_id=1,
        base_model="efficientnet_b0",
        model_name="cancel_from_paused",
        task_type="classification",
        celery_task_id="test-celery-paused-1",
        state="PAUSED",
        progress=50.0,
        epochs=10,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with patch("app.tasks.workers.control_signals.request_cancel") as mock_cancel:
        mock_cancel.return_value = True
        with patch("app.tasks.workers.celery_app.celery_app.control.revoke"):
            resp = await client.post(
                f"/api/training/jobs/{job.id}/cancel",
                headers=auth_headers,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body.get("success") is True
            assert body.get("state") == "CANCELED"
            assert mock_cancel.called

    await db_session.refresh(job)
    assert job.state == "CANCELED"


@pytest.mark.asyncio
async def test_cancel_permission_denied_for_other_user(
    client, auth_headers, db_session, test_user
):
    """非 owner 不可取消他人任务 (越权防护)"""
    # 创建另一个用户
    from app.admin.model.user import User
    from app.middleware.security.security import hash_password
    other_user = User(
        username="other_user_1",
        email="other@example.com",
        password_hash=hash_password("otherpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    # 创建一个属于 other_user 的 job
    job = TrainingJob(
        user_id=other_user.id,
        dataset_id=1,
        base_model="efficientnet_b0",
        model_name="other_user_job",
        task_type="classification",
        celery_task_id="other-user-celery-1",
        state="PROGRESS",
        progress=30.0,
        epochs=10,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # test_user 试图取消 → 403
    resp = await client.post(
        f"/api/training/jobs/{job.id}/cancel",
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_cancel_404_for_nonexistent_job(client, auth_headers):
    """不存在的 job 返回 404"""
    resp = await client.post(
        "/api/training/jobs/9999999/cancel",
        headers=auth_headers,
    )
    assert resp.status_code == 404
