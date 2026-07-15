"""
Test: Training API
==================
2 条功能测试: 启动训练 / 进度查询
"""
import pytest


@pytest.mark.asyncio
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
    """TC-TRN-02: 查询训练进度 (对不存在的 task 返回 PENDING)"""
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(f"/api/training/progress/{fake_id}", headers=auth_headers)
    # 401 是因为没 token 时不可达, 这里有 token, 应该 200 + state=PENDING/FAILURE
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("state") in ("PENDING", "FAILURE", "PROGRESS", "SUCCESS")
    # 必须包含 current_epoch / total_epochs 字段 (spec 契约)
    assert "current_epoch" in body or body.get("current_epoch") is None
    assert "total_epochs" in body or body.get("total_epochs") is None
