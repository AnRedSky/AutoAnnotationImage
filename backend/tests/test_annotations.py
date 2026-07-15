"""
Test: Annotation API
====================
3 条功能测试: 保存 / 状态变更 / 统计
"""
import io
import pytest
from PIL import Image


def _make_png_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _create_dataset_with_image(client, auth_headers, name="ann_ds") -> tuple:
    """辅助: 创建一个数据集 + 1 张图, 返回 (dataset_id, image_id, label_id)"""
    ds = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": name, "task_type": "classification"},
    )
    ds_id = ds.json()["id"]
    cat_resp = await client.post(
        f"/api/datasets/{ds_id}/categories",
        headers=auth_headers,
        json={"name": "label_A"},
    )
    label_id = cat_resp.json()["id"] if cat_resp.status_code in (200, 201) else None
    up = await client.post(
        f"/api/images/upload/{ds_id}",
        headers=auth_headers,
        files=[("files", ("a.png", _make_png_bytes(), "image/png"))],
    )
    items = up.json().get("items", [])
    image_id = items[0]["filename"] and up.json().get("uploaded", 0) > 0
    # 通过 list 接口拿 image_id
    if image_id:
        lst = await client.get(
            f"/api/images/list/{ds_id}?page=1&page_size=5", headers=auth_headers
        )
        items = lst.json().get("items", [])
        image_id = items[0]["id"] if items else None
    return ds_id, image_id, label_id


@pytest.mark.asyncio
async def test_save_annotation_confirm(client, auth_headers, temp_upload_dir):
    """TC-ANN-01: 保存标注 (确认)"""
    ds_id, image_id, label_id = await _create_dataset_with_image(
        client, auth_headers, "confirm_ds"
    )
    assert image_id is not None, "Failed to get image_id"
    resp = await client.post(
        "/api/annotations/save",
        headers=auth_headers,
        json={
            "image_id": image_id,
            "label_id": label_id,
            "time_spent_ms": 1500,
            "is_confirm": True,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    # API 返回 success + new_status
    assert body.get("success") is True
    assert body.get("new_status") in ("human_confirmed", "human_corrected")


@pytest.mark.asyncio
async def test_save_annotation_correct(client, auth_headers, temp_upload_dir):
    """TC-ANN-02: 保存标注 (强制修正, is_confirm=False)"""
    ds_id, image_id, label_id = await _create_dataset_with_image(
        client, auth_headers, "correct_ds"
    )
    assert image_id is not None
    resp = await client.post(
        "/api/annotations/save",
        headers=auth_headers,
        json={
            "image_id": image_id,
            "label_id": label_id,
            "time_spent_ms": 3000,
            "is_confirm": False,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body.get("success") is True
    assert body.get("new_status") == "human_corrected"


@pytest.mark.asyncio
async def test_annotation_stats(client, auth_headers, temp_upload_dir):
    """TC-ANN-03: 标注统计"""
    ds_id, image_id, label_id = await _create_dataset_with_image(
        client, auth_headers, "stats_ds"
    )
    if image_id:
        # 先标注一次
        await client.post(
            "/api/annotations/save",
            headers=auth_headers,
            json={
                "image_id": image_id,
                "label_id": label_id,
                "time_spent_ms": 1000,
                "is_confirm": True,
            },
        )
    resp = await client.get(f"/api/annotations/stats/{ds_id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    # API 返回 status_counts + total_annotations + ...
    assert "status_counts" in body
    assert "total_annotations" in body
    assert "avg_seconds_per_image" in body
