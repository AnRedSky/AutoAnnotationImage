"""
Test: New Image Endpoints
=========================
新增端点回归测试:
  - GET  /api/images/{id}              单图详情
  - DEL  /api/images/{id}              单图删除
  - POST /api/images/batch-delete      批量删除
  - GET  /api/files/{id}               原图二进制
  - GET  /api/files/{id}/thumbnail     缩略图
  - GET  /api/stats/dataset/{id}       数据集统计（含 AI 节省时间）
  - GET  /api/annotations/list/{id}    标注审计
"""
import io
import pytest
from PIL import Image


def _make_png_bytes(color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", (64, 64), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_dataset_with_images(client, headers, count=3, with_categories=True):
    """辅助: 创建一个数据集 + 类别 + N 张图"""
    payload = {"name": f"detail_ds_{count}", "task_type": "classification"}
    if with_categories:
        payload["category_names"] = ["cat_a", "cat_b", "cat_c"]
    r = await client.post("/api/datasets", headers=headers, json=payload)
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]
    files = [
        ("files", (f"img_{i}.png", _make_png_bytes((i * 30, i * 30, i * 30)), "image/png"))
        for i in range(count)
    ]
    r2 = await client.post(f"/api/images/upload/{ds_id}", headers=headers, files=files)
    assert r2.status_code in (200, 201), r2.text
    return ds_id


@pytest.mark.asyncio
async def test_image_detail(client, auth_headers, temp_upload_dir):
    """TC-IMG-DETAIL: GET /api/images/{id} 返回图片详情 + 元数据"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    items = lst.json()["items"]
    assert len(items) == 2
    img_id = items[0]["id"]

    r = await client.get(f"/api/images/{img_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == img_id
    assert body["dataset_id"] == ds_id
    assert "filename" in body
    assert "status" in body
    assert "file_url" in body
    # annotation_history 必须为列表
    assert isinstance(body["annotation_history"], list)


@pytest.mark.asyncio
async def test_image_detail_404(client, auth_headers, temp_upload_dir):
    """TC-IMG-DETAIL-404: 不存在的图片返回 404"""
    r = await client.get("/api/images/99999", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_ids_default(client, auth_headers, temp_upload_dir):
    """v3.5.0 TC-IMG-LIST-IDS: GET /api/images/ids/{dataset_id} 返回 id 列表 + total

    验证:
    - 默认 desc 排序, items 为 id 数组 (按 id 降序)
    - total 等于真实图片数
    - 不带 status 时返回数据集下全部图
    - 不返回 filename / width 等其他字段 (轻量级)
    """
    ds_id = await _make_dataset_with_images(client, auth_headers, count=5)
    r = await client.get(f"/api/images/ids/{ds_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 字段完整
    assert "items" in body
    assert "total" in body
    assert body["total"] == 5
    assert body["max_ids"] == 2000
    assert body["order"] == "desc"
    assert body["truncated"] is False
    # items 是 int 数组, 长度等于 total
    assert len(body["items"]) == 5
    assert all(isinstance(x, int) for x in body["items"])
    # desc 排序: id 降序
    assert body["items"] == sorted(body["items"], reverse=True)


@pytest.mark.asyncio
async def test_list_ids_with_status_filter(client, auth_headers, temp_upload_dir):
    """v3.5.0 TC-IMG-LIST-IDS-STATUS: status 过滤生效

    验证:
    - status=pending 仅返回 status=pending 的图
    - 不合格的图被排除
    """
    ds_id = await _make_dataset_with_images(client, auth_headers, count=3)
    # 全部新上传图都是 pending
    r = await client.get(f"/api/images/ids/{ds_id}", params={"status": "pending"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


@pytest.mark.asyncio
async def test_list_ids_max_truncation(client, auth_headers, temp_upload_dir):
    """v3.5.0 TC-IMG-LIST-IDS-TRUNC: max_ids 截断

    验证:
    - max_ids=2 时, items 最多 2 个, 但 total 仍为真实数
    - truncated=true 标记生效
    """
    ds_id = await _make_dataset_with_images(client, auth_headers, count=5)
    r = await client.get(f"/api/images/ids/{ds_id}", params={"max_ids": 2}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_list_ids_order_asc(client, auth_headers, temp_upload_dir):
    """v3.5.0 TC-IMG-LIST-IDS-ORDER: order=asc 时 id 升序

    验证:
    - order=asc 排序: id 升序
    - 与 desc 顺序相反
    """
    ds_id = await _make_dataset_with_images(client, auth_headers, count=4)
    r = await client.get(f"/api/images/ids/{ds_id}", params={"order": "asc"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order"] == "asc"
    assert body["items"] == sorted(body["items"])


@pytest.mark.asyncio
async def test_list_ids_404_dataset(client, auth_headers):
    """v3.5.0 TC-IMG-LIST-IDS-404: 不存在的数据集返回 404"""
    r = await client.get("/api/images/ids/99999", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_image_delete(client, auth_headers, temp_upload_dir):
    """TC-IMG-DELETE: 删除单图后, list 中不再有"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_id = lst.json()["items"][0]["id"]
    initial_count = lst.json()["total"]

    r = await client.delete(f"/api/images/{img_id}", headers=auth_headers)
    assert r.status_code in (200, 204), r.text

    lst2 = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    assert lst2.json()["total"] == initial_count - 1

    # 二次删除应 404
    r2 = await client.delete(f"/api/images/{img_id}", headers=auth_headers)
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_image_batch_delete(client, auth_headers, temp_upload_dir):
    """TC-IMG-BATCH-DELETE: 批量删除多张"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=5)
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    items = lst.json()["items"]
    ids_to_delete = [items[0]["id"], items[1]["id"], items[2]["id"]]

    r = await client.post("/api/images/batch-delete", headers=auth_headers, json=ids_to_delete)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 3
    assert body["missing"] == 0

    lst2 = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    assert lst2.json()["total"] == 2


@pytest.mark.asyncio
async def test_image_batch_delete_empty(client, auth_headers):
    """TC-IMG-BATCH-DELETE-EMPTY: 空数组视为空操作, 返回 200 + 全零计数

    历史变更:
    - 旧版: 抛 HTTPException(400), 接口契约为「拒绝空数组」
    - v3.x: 改为「空操作 = 200 + 全零计数」, 与「空选区直接清空」语义一致,
      前端批量选择取消时无需特判, 直接调用即可
    """
    r = await client.post("/api/images/batch-delete", headers=auth_headers, json=[])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 0
    assert body["missing"] == 0


@pytest.mark.asyncio
async def test_image_batch_delete_too_many(client, auth_headers):
    """TC-IMG-BATCH-DELETE-MAX: 超过 500 返回 400"""
    r = await client.post(
        "/api/images/batch-delete",
        headers=auth_headers,
        json=list(range(1, 502)),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_list_pagination(client, auth_headers, temp_upload_dir):
    """TC-IMG-PAGINATION: 分页参数生效"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=6)
    r = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size=2",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 6
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["page_size"] == 2

    r2 = await client.get(
        f"/api/images/list/{ds_id}?page=3&page_size=2",
        headers=auth_headers,
    )
    assert r2.json()["page"] == 3
    assert len(r2.json()["items"]) == 2


@pytest.mark.asyncio
async def test_list_filter_by_status(client, auth_headers, temp_upload_dir):
    """TC-IMG-FILTER: 按 status 过滤"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=3)
    # 上传时所有图 status=pending
    r = await client.get(
        f"/api/images/list/{ds_id}?status=pending",
        headers=auth_headers,
    )
    body = r.json()
    for item in body["items"]:
        assert item["status"] == "pending"

    # 过滤 ai_labeled 应该为空
    r2 = await client.get(
        f"/api/images/list/{ds_id}?status=ai_labeled",
        headers=auth_headers,
    )
    assert r2.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_multi_status_in_query(client, auth_headers, temp_upload_dir, db_session):
    """v3.6.1 TC-IMG-LIST-MULTI: status 多值 (逗号分隔) 走 IN 查询

    修复背景: 「已人工标注」状态卡聚合 human_confirmed + human_corrected
    - 旧实现只支持单值 Image.status == status, 传 "human_confirmed,human_corrected" 永远不匹配
    - 新实现: status 含逗号 → 走 Image.status.in_([...])

    验证:
    - 上传 3 张图 (全 pending)
    - 改 1 张为 human_confirmed, 1 张为 human_corrected, 1 张保持 pending
    - GET /api/images/list?status=human_confirmed,human_corrected 应返回 2 张
    - 排除不合格图
    """
    from sqlalchemy import select
    from app.tasks.model.image import Image
    from app.tasks.model.image import (
        IMAGE_STATUS_HUMAN_CONFIRMED,
        IMAGE_STATUS_HUMAN_CORRECTED,
    )

    ds_id = await _make_dataset_with_images(client, auth_headers, count=3)
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    items = lst.json()["items"]
    assert len(items) == 3

    # 直接改 DB: items[0] → human_confirmed, items[1] → human_corrected
    rows = (await db_session.execute(
        select(Image).where(Image.id.in_([items[0]["id"], items[1]["id"], items[2]["id"]]))
    )).scalars().all()
    by_id = {r.id: r for r in rows}
    by_id[items[0]["id"]].status = IMAGE_STATUS_HUMAN_CONFIRMED
    by_id[items[1]["id"]].status = IMAGE_STATUS_HUMAN_CORRECTED
    # items[2] 保持 pending
    await db_session.commit()

    # 多状态 IN 查询
    r = await client.get(
        f"/api/images/list/{ds_id}",
        params={"status": f"{IMAGE_STATUS_HUMAN_CONFIRMED},{IMAGE_STATUS_HUMAN_CORRECTED}"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    returned_statuses = {item["status"] for item in body["items"]}
    assert returned_statuses == {IMAGE_STATUS_HUMAN_CONFIRMED, IMAGE_STATUS_HUMAN_CORRECTED}
    # pending 图不应在结果中
    assert items[2]["id"] not in {item["id"] for item in body["items"]}


@pytest.mark.asyncio
async def test_list_ids_multi_status_in_query(client, auth_headers, temp_upload_dir, db_session):
    """v3.6.1 TC-IMG-LIST-IDS-MULTI: list_ids 也支持 status 多值 IN 查询

    验证:
    - /api/images/ids 接收 status=human_confirmed,human_corrected 时返回两种状态的并集
    - 同时支持 max_ids 限制
    """
    from sqlalchemy import select
    from app.tasks.model.image import Image
    from app.tasks.model.image import (
        IMAGE_STATUS_HUMAN_CONFIRMED,
        IMAGE_STATUS_HUMAN_CORRECTED,
    )

    ds_id = await _make_dataset_with_images(client, auth_headers, count=4)
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    items = lst.json()["items"]

    rows = (await db_session.execute(
        select(Image).where(Image.id.in_([it["id"] for it in items]))
    )).scalars().all()
    by_id = {r.id: r for r in rows}
    by_id[items[0]["id"]].status = IMAGE_STATUS_HUMAN_CONFIRMED
    by_id[items[1]["id"]].status = IMAGE_STATUS_HUMAN_CORRECTED
    by_id[items[2]["id"]].status = IMAGE_STATUS_HUMAN_CONFIRMED
    # items[3] 保持 pending
    await db_session.commit()

    # 多状态 IN 查询 → 3 张
    r = await client.get(
        f"/api/images/ids/{ds_id}",
        params={"status": f"{IMAGE_STATUS_HUMAN_CONFIRMED},{IMAGE_STATUS_HUMAN_CORRECTED}"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["status_filter"] == f"{IMAGE_STATUS_HUMAN_CONFIRMED},{IMAGE_STATUS_HUMAN_CORRECTED}"
    # pending 不在结果中
    assert items[3]["id"] not in body["items"]


@pytest.mark.asyncio
async def test_list_excludes_unqualified_in_multi_status(client, auth_headers, temp_upload_dir, db_session):
    """v3.6.1 TC-IMG-LIST-MULTI-UNQUALIFIED: 多状态 IN 查询同时排除不合格图

    验证: 不合格图 (quality_flag=unqualified) 即使原 status 在 IN 列表中也被排除
    - 与 list 单状态查询保持一致
    """
    from sqlalchemy import select
    from app.tasks.model.image import Image
    from app.tasks.model.image import (
        IMAGE_STATUS_HUMAN_CONFIRMED,
        IMAGE_STATUS_HUMAN_CORRECTED,
        IMAGE_QUALITY_UNQUALIFIED,
    )

    ds_id = await _make_dataset_with_images(client, auth_headers, count=3)
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    items = lst.json()["items"]

    rows = (await db_session.execute(
        select(Image).where(Image.id.in_([it["id"] for it in items]))
    )).scalars().all()
    by_id = {r.id: r for r in rows}
    by_id[items[0]["id"]].status = IMAGE_STATUS_HUMAN_CONFIRMED
    by_id[items[1]["id"]].status = IMAGE_STATUS_HUMAN_CORRECTED
    by_id[items[2]["id"]].status = IMAGE_STATUS_HUMAN_CONFIRMED
    by_id[items[2]["id"]].quality_flag = IMAGE_QUALITY_UNQUALIFIED  # 不合格
    await db_session.commit()

    # 多状态 IN 查询 → 应只返回 2 张 (排除不合格的 items[2])
    r = await client.get(
        f"/api/images/list/{ds_id}",
        params={"status": f"{IMAGE_STATUS_HUMAN_CONFIRMED},{IMAGE_STATUS_HUMAN_CORRECTED}"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert items[2]["id"] not in {item["id"] for item in body["items"]}


@pytest.mark.asyncio
async def test_file_serving(client, auth_headers, temp_upload_dir):
    """TC-FILES: GET /api/files/{id} 返回图片二进制"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=1)
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_id = lst.json()["items"][0]["id"]

    r = await client.get(f"/api/files/{img_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/")
    assert len(r.content) > 0


@pytest.mark.asyncio
async def test_file_serving_404(client, auth_headers, temp_upload_dir):
    """TC-FILES-404: 不存在的图片返回 404"""
    r = await client.get("/api/files/99999", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_thumbnail_serving(client, auth_headers, temp_upload_dir):
    """TC-THUMB: GET /api/files/{id}/thumbnail 返回缩略图 (JPEG)"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=1)
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_id = lst.json()["items"][0]["id"]

    r = await client.get(
        f"/api/files/{img_id}/thumbnail?size=120", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.headers.get("content-type") == "image/jpeg"
    # 缩略图应能 PIL 解码
    from PIL import Image as PILImage
    from io import BytesIO
    img = PILImage.open(BytesIO(r.content))
    assert img.format == "JPEG"
    # 最长边 <= 120
    assert max(img.size) <= 120


@pytest.mark.asyncio
async def test_dataset_stats(client, auth_headers, temp_upload_dir):
    """TC-STATS: GET /api/stats/dataset/{id} 返回 AI 节省时间等核心指标"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=4)
    r = await client.get(f"/api/stats/dataset/{ds_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "status_counts" in body
    assert "category_distribution" in body
    assert "annotation" in body
    ann = body["annotation"]
    # 关键字段必须存在
    for key in (
        "total_annotations", "actual_seconds", "avg_seconds_per_image",
        "ai_labeled_count", "human_confirmed_count", "human_corrected_count",
        "estimated_saved_seconds", "estimated_saved_ratio",
    ):
        assert key in ann, f"missing {key}"


@pytest.mark.asyncio
async def test_dataset_stats_404(client, auth_headers):
    """TC-STATS-404: 不存在的数据集返回 404"""
    r = await client.get("/api/stats/dataset/99999", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_annotations_list(client, auth_headers, temp_upload_dir):
    """TC-ANNO-LIST: 标注审计日志分页查询"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    # 创建标注: 需要先有 label_id
    cats = await client.get(f"/api/datasets/{ds_id}/categories", headers=auth_headers)
    label_id = cats.json()["items"][0]["id"]
    lst = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_id = lst.json()["items"][0]["id"]

    # 写一条标注
    r = await client.post(
        "/api/annotations/save",
        headers=auth_headers,
        json={
            "image_id": img_id,
            "label_id": label_id,
            "time_spent_ms": 1500,
            "is_confirm": False,
        },
    )
    assert r.status_code == 200

    # 列出
    r2 = await client.get(
        f"/api/annotations/list/{ds_id}?page=1&page_size=10",
        headers=auth_headers,
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["total"] >= 1
    assert len(body["items"]) >= 1
    item = body["items"][0]
    assert item["action"] in ("confirm", "correct")
    assert item["image_id"] == img_id


@pytest.mark.asyncio
async def test_dataset_categories(client, auth_headers):
    """TC-CAT: 创建数据集时批量建类别 + 列类别"""
    r = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={
            "name": "cat_ds",
            "task_type": "classification",
            "category_names": ["a", "b", "c", "d"],
        },
    )
    assert r.status_code in (200, 201)
    ds_id = r.json()["id"]
    assert r.json()["category_count"] == 4

    r2 = await client.get(f"/api/datasets/{ds_id}/categories", headers=auth_headers)
    assert r2.status_code == 200
    items = r2.json()["items"]
    assert len(items) == 4
    names = [c["name"] for c in items]
    assert names == ["a", "b", "c", "d"]


@pytest.mark.asyncio
async def test_add_category_after_create(client, auth_headers):
    """TC-CAT-ADD: 创建后追加类别"""
    r = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "add_cat_ds", "task_type": "classification"},
    )
    ds_id = r.json()["id"]
    r2 = await client.post(
        f"/api/datasets/{ds_id}/categories",
        headers=auth_headers,
        json={"name": "new_cat", "color": "#67c23a"},
    )
    assert r2.status_code == 200
    assert r2.json()["name"] == "new_cat"
    # 再次 GET 应能看到
    r3 = await client.get(f"/api/datasets/{ds_id}/categories", headers=auth_headers)
    items = r3.json()["items"]
    assert any(c["name"] == "new_cat" for c in items)
