"""
Test: Dataset API
=================
4 条功能测试: 创建 / 列表 / 详情 / 删除
"""
import pytest


@pytest.mark.asyncio
async def test_create_dataset(client, auth_headers):
    """TC-DS-01: 创建数据集 + 批量创建类别"""
    resp = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={
            "name": "test_garbage_5",
            "description": "Test dataset",
            "task_type": "classification",
            "category_names": ["plastic", "paper", "metal"],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["name"] == "test_garbage_5"
    # API 返回 category_count + categories_created
    assert body["category_count"] == 3
    assert "categories_created" in body


@pytest.mark.asyncio
async def test_list_datasets(client, auth_headers, test_dataset_factory):
    """TC-DS-02: 列出数据集"""
    # 先创建 2 个
    await test_dataset_factory(2, name_prefix="list_ds")
    resp = await client.get("/api/datasets", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    items = body if isinstance(body, list) else body.get("items", [])
    assert len(items) >= 2


@pytest.mark.asyncio
async def test_get_dataset_detail(client, auth_headers):
    """TC-DS-03: 获取数据集详情"""
    create = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "detail_ds", "task_type": "classification"},
    )
    ds_id = create.json()["id"]
    resp = await client.get(f"/api/datasets/{ds_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == ds_id


@pytest.mark.asyncio
async def test_delete_dataset(client, auth_headers):
    """TC-DS-04: 删除数据集"""
    create = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "to_delete", "task_type": "classification"},
    )
    ds_id = create.json()["id"]
    resp = await client.delete(f"/api/datasets/{ds_id}", headers=auth_headers)
    assert resp.status_code in (200, 204)
    # 再次 GET 应 404
    get_resp = await client.get(f"/api/datasets/{ds_id}", headers=auth_headers)
    assert get_resp.status_code == 404
