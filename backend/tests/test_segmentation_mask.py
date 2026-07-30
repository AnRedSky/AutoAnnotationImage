"""
v2.0.0 S5.1 测试: 图像分割 mask CRUD
====================================

覆盖 4 条用例 (S5.1 验收):
 1. test_mask_upload_ok             上传 P-mode 索引 PNG, 返回 category_pixel_counts
 2. test_mask_get_metadata          GET 返回 JSON 元数据 (含 file_exists, file_size)
 3. test_mask_get_download          GET ?download=true 返回 PNG 二进制
 4. test_mask_delete                DELETE 同时清理磁盘文件
"""
import io

import pytest
from PIL import Image as PILImage

from app.annotation.model.segmentation_mask import SegmentationMask


# ============== 工具 ==============

def _make_png_bytes(mode: str = "P", w: int = 32, h: int = 32,
                    bg_value: int = 0, fg_value: int = 1) -> bytes:
    """
    生成 mode 模式的 PNG bytes
    - mode='P': 索引图, 左上 1/4 区域填 fg_value, 其余 bg_value
    - mode='L': 灰度图, 同上 (灰度值就是像素值)
    """
    img = PILImage.new(mode, (w, h), color=bg_value)
    if mode == "P":
        # P-mode 必须配调色板
        palette = [0] * 768
        # 0=黑, 1=白, 2=红
        palette[0:3] = [0, 0, 0]
        palette[3:6] = [255, 255, 255]
        palette[6:9] = [255, 0, 0]
        img.putpalette(palette)
    # 画一块 fg
    for y in range(0, h // 2):
        for x in range(0, w // 2):
            img.putpixel((x, y), fg_value)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_segmentation_dataset(
    client, auth_headers, n_images: int = 1,
    image_w: int = 64, image_h: int = 64,
    n_categories: int = 2,
):
    """
    创建 segmentation 数据集 + n_images 张图 + n_categories 个类别
    返回 (ds_id, cat_ids: list[int], img_ids: list[int])
    """
    # 1) 数据集
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "ds_seg", "task_type": "segmentation"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]

    # 2) 类别
    cat_ids = []
    for i in range(n_categories):
        r = await client.post(
            f"/api/datasets/{ds_id}/categories", headers=auth_headers,
            json={"name": f"cls_{i}"},
        )
        assert r.status_code in (200, 201), r.text
        cat_ids.append(r.json()["id"])

    # 3) 上传图 (不同颜色避免 hash 去重)
    files = [
        ("files", (f"img_{i}.png",
                   _make_png_bytes("RGB", image_w, image_h,
                                   ((i * 73 + 50) % 256,
                                    (i * 137 + 80) % 256,
                                    200)),
                   "image/png"))
        for i in range(n_images)
    ]
    r = await client.post(
        f"/api/images/upload/{ds_id}", headers=auth_headers, files=files,
    )
    assert r.status_code in (200, 201), r.text
    assert r.json().get("uploaded", 0) >= n_images, r.text

    # 4) 列图拿 id
    r = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size={n_images}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= n_images, r.text
    img_ids = [it["id"] for it in items[:n_images]]
    return ds_id, cat_ids, img_ids


# ============== 测试 4 条 ==============

@pytest.mark.asyncio
async def test_mask_upload_ok(client, auth_headers, temp_upload_dir):
    """上传 P-mode 索引 PNG -> 200 + category_pixel_counts"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=1, n_categories=2,
    )
    assert len(img_ids) == 1

    # 像素值最大 = 1, 类别 cat_ids[1] 即为像素值 1 对应的类别
    # 但 ORM Category.id 取决于创建顺序, 这里用 cat_ids 的 max 作为最大允许值
    max_cat_id = max(cat_ids)
    # mask 像素值 0/1
    png_bytes = _make_png_bytes("P", w=32, h=32, bg_value=0, fg_value=1)

    r = await client.post(
        f"/api/segmentation/masks/upload/{img_ids[0]}",
        headers=auth_headers,
        files={"file": ("mask.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["image_id"] == img_ids[0]
    assert body["width"] == 32
    assert body["height"] == 32
    assert "mask_path" in body and body["mask_path"].endswith(".png")
    # counts: 0 和 1 都出现 (JSON 序列化后 key 是 str)
    counts = body["category_pixel_counts"]
    assert "0" in counts
    assert "1" in counts
    assert counts["0"] + counts["1"] == 32 * 32

    # 复核: DB 里能看到
    from sqlalchemy import select
    from app.database import get_db as _gd
    sess = client._transport.app.dependency_overrides[_gd]
    # 用查询的另一种方式: 通过 ORM get
    m = (await client.get(
        f"/api/segmentation/masks/{img_ids[0]}", headers=auth_headers,
    )).json()
    assert m["image_id"] == img_ids[0]
    assert m["width"] == 32


@pytest.mark.asyncio
async def test_mask_get_metadata(client, auth_headers, temp_upload_dir):
    """GET mask 返回 JSON 元数据"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=1, n_categories=2,
    )
    png_bytes = _make_png_bytes("L", w=24, h=24, bg_value=0, fg_value=1)

    r = await client.post(
        f"/api/segmentation/masks/upload/{img_ids[0]}",
        headers=auth_headers,
        files={"file": ("mask.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text

    # GET 元数据
    r = await client.get(
        f"/api/segmentation/masks/{img_ids[0]}", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_exists"] is True
    assert body["file_size"] > 0
    assert body["width"] == 24
    assert body["height"] == 24
    assert body["source"] == "human"
    assert "created_at" in body
    assert "updated_at" in body


@pytest.mark.asyncio
async def test_mask_get_download(client, auth_headers, temp_upload_dir):
    """GET ?download=true 返回 PNG 二进制"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=1, n_categories=2,
    )
    original_bytes = _make_png_bytes("P", w=16, h=16, bg_value=0, fg_value=1)
    r = await client.post(
        f"/api/segmentation/masks/upload/{img_ids[0]}",
        headers=auth_headers,
        files={"file": ("mask.png", original_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text

    # GET 二进制
    r = await client.get(
        f"/api/segmentation/masks/{img_ids[0]}?download=true",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    # 二进制能被 PIL 解析回 (16, 16) P/L mode
    pil = PILImage.open(io.BytesIO(r.content))
    assert pil.size == (16, 16)


@pytest.mark.asyncio
async def test_mask_delete(client, auth_headers, temp_upload_dir):
    """DELETE mask 同时清理磁盘文件"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=1, n_categories=2,
    )
    png_bytes = _make_png_bytes("P", w=20, h=20, bg_value=0, fg_value=1)
    r = await client.post(
        f"/api/segmentation/masks/upload/{img_ids[0]}",
        headers=auth_headers,
        files={"file": ("mask.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    mask_id = r.json()["id"]

    # 复核文件存在
    r = await client.get(
        f"/api/segmentation/masks/{img_ids[0]}", headers=auth_headers,
    )
    assert r.json()["file_exists"] is True

    # DELETE
    r = await client.delete(
        f"/api/segmentation/masks/{mask_id}", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["deleted_id"] == mask_id
    assert body["file_deleted"] is True

    # 复核: GET 应返回 200 + file_exists:False (v2.5.0-s12.7 契约: mask 不存在不抛 404)
    r = await client.get(
        f"/api/segmentation/masks/{img_ids[0]}", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] is None
    assert body["file_exists"] is False
