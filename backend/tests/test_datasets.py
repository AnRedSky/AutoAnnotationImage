"""
Test: Dataset API
=================
4 条功能测试: 创建 / 列表 / 详情 / 删除
+ 1 条: 类别列表实时统计 (v2.x)
"""
import pytest
from sqlalchemy import select

from app.tasks.model.image import Image
from app.tasks.model.category import Category


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


@pytest.mark.asyncio
async def test_list_categories_with_stats(
    client, auth_headers, db_session, temp_upload_dir
):
    """TC-DS-05: 类别列表返回实时统计 (v2.x: 不依赖 Category.sample_count 缓存)

    构造场景:
      - 1 个数据集, 3 个类别 (cat / dog / bird)
      - 5 张图, 其中:
        · 2 张 final_label=cat, status=human_confirmed (人工已确认)
        · 1 张 final_label=dog, status=ai_labeled        (AI 已标)
        · 1 张 final_label=bird, status=ai_labeled       (AI 已标)
        · 1 张 final_label=None, ai_prediction.top1=cat  (AI 候选, 未落标)
        · (再放 1 张完全 pending 的图, 任何桶都不应计数)

    期望:
      cat:  human=2, ai=0, candidate=1, sample_count=2
      dog:  human=0, ai=1, candidate=0, sample_count=1
      bird: human=0, ai=1, candidate=0, sample_count=1
    """
    # 1) 建数据集 + 3 个类别
    ds = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={
            "name": "stats_ds",
            "task_type": "classification",
            "category_names": ["cat", "dog", "bird"],
        },
    )
    assert ds.status_code in (200, 201), ds.text
    ds_id = ds.json()["id"]

    # 取类别 id
    cats_resp = await client.get(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers
    )
    assert cats_resp.status_code == 200, cats_resp.text
    cats = {c["name"]: c["id"] for c in cats_resp.json()["items"]}
    assert set(cats.keys()) == {"cat", "dog", "bird"}

    # 2) 构造 6 张图, 各种状态
    images_seed = [
        # (filename, status, final_label_id, ai_prediction)
        ("a.png", "human_confirmed", cats["cat"], None),
        ("b.png", "human_confirmed", cats["cat"], None),
        ("c.png", "ai_labeled",      cats["dog"], {"top1": "dog", "top1_conf": 0.88}),
        ("d.png", "ai_labeled",      cats["bird"], {"top1": "bird", "top1_conf": 0.92}),
        ("e.png", "pending",         None,        {"top1": "cat", "top1_conf": 0.55}),
        ("f.png", "pending",         None,        None),
    ]
    for fn, status, fid, pred in images_seed:
        img = Image(
            dataset_id=ds_id,
            filename=fn,
            storage_path=f"{ds_id}/{fn}",
            file_size=1024,
            file_hash=f"hash_{fn}",
            status=status,
            final_label_id=fid,
            ai_prediction=pred,
        )
        db_session.add(img)
    await db_session.commit()

    # 3) 拉类别列表, 校验统计
    resp = await client.get(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = {c["name"]: c for c in body["items"]}
    assert set(items.keys()) == {"cat", "dog", "bird"}

    # cat: 2 人工 + 0 AI + 1 候选 = sample 2
    assert items["cat"]["human_labeled_count"] == 2
    assert items["cat"]["ai_labeled_count"] == 0
    assert items["cat"]["ai_candidate_count"] == 1
    assert items["cat"]["sample_count"] == 2

    # dog: 0 人工 + 1 AI + 0 候选 = sample 1
    assert items["dog"]["human_labeled_count"] == 0
    assert items["dog"]["ai_labeled_count"] == 1
    assert items["dog"]["ai_candidate_count"] == 0
    assert items["dog"]["sample_count"] == 1

    # bird: 同 dog
    assert items["bird"]["human_labeled_count"] == 0
    assert items["bird"]["ai_labeled_count"] == 1
    assert items["bird"]["ai_candidate_count"] == 0
    assert items["bird"]["sample_count"] == 1


@pytest.mark.asyncio
async def test_list_categories_empty(client, auth_headers):
    """TC-DS-06: 类别为空时, 接口返回空 items 列表 (不报错)"""
    ds = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "empty_cats_ds", "task_type": "classification"},
    )
    ds_id = ds.json()["id"]
    resp = await client.get(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": []}
