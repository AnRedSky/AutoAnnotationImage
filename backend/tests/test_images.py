"""
Test: Image Upload & Auto-Label API
====================================
3 条功能测试: 上传 / 去重 / 自动标注
"""
import io
import pytest
from PIL import Image


def _make_png_bytes(color=(255, 0, 0)) -> bytes:
    """生成一张测试 PNG 的字节流"""
    img = Image.new("RGB", (32, 32), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_upload_images(client, auth_headers, temp_upload_dir):
    """TC-IMG-01: 批量上传 3 张图"""
    create = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "upload_ds", "task_type": "classification"},
    )
    ds_id = create.json()["id"]
    files = [
        ("files", ("a.png", _make_png_bytes((255, 0, 0)), "image/png")),
        ("files", ("b.png", _make_png_bytes((0, 255, 0)), "image/png")),
        ("files", ("c.png", _make_png_bytes((0, 0, 255)), "image/png")),
    ]
    resp = await client.post(
        f"/api/images/upload/{ds_id}",
        headers=auth_headers,
        files=files,
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    # API 返回 uploaded / duplicates / total
    assert body.get("uploaded", 0) >= 3 or body.get("total", 0) >= 3
    assert len(body.get("items", [])) == 3


@pytest.mark.asyncio
async def test_upload_dedup(client, auth_headers, temp_upload_dir):
    """TC-IMG-02: 同一 hash 不重复入库"""
    create = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "dedup_ds", "task_type": "classification"},
    )
    ds_id = create.json()["id"]
    same_bytes = _make_png_bytes((128, 128, 128))
    files = [("files", ("x.png", same_bytes, "image/png"))]
    r1 = await client.post(f"/api/images/upload/{ds_id}", headers=auth_headers, files=files)
    r2 = await client.post(f"/api/images/upload/{ds_id}", headers=auth_headers, files=files)
    b1 = r1.json()
    b2 = r2.json()
    # 第一次 added=1, 第二次 added=0 (去重)
    assert b1.get("uploaded", 0) == 1, f"First upload should add 1: {b1}"
    assert b2.get("uploaded", 0) == 0, f"Second upload should be deduped: {b2}"
    assert b2.get("duplicates", 0) >= 1


@pytest.mark.asyncio
async def test_list_images(client, auth_headers, temp_upload_dir):
    """TC-IMG-03: 分页查询图片列表"""
    create = await client.post(
        "/api/datasets",
        headers=auth_headers,
        json={"name": "list_ds", "task_type": "classification"},
    )
    ds_id = create.json()["id"]
    # 上传 2 张
    files = [
        ("files", ("p1.png", _make_png_bytes(), "image/png")),
        ("files", ("p2.png", _make_png_bytes((50, 50, 50)), "image/png")),
    ]
    await client.post(f"/api/images/upload/{ds_id}", headers=auth_headers, files=files)
    resp = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size=10", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    items = body if isinstance(body, list) else body.get("items", [])
    assert len(items) >= 2
