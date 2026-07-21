"""
v2.0.0 S8 E2E 集成测试: 检测 + 分割 任务类型全链路
==================================================

覆盖 10 条用例 (S8 验收):

**Detection 全链路 (5):**
 1. test_e2e_detection_create_dataset       创建 detection 数据集 + 类别
 2. test_e2e_detection_upload_bbox          上传 bbox 标注
 3. test_e2e_detection_yolo_export          YOLO-det 导出 zip 完整结构
 4. test_e2e_detection_coco_export          COCO-det JSON 完整字段
 5. test_e2e_detection_bbox_crud            bbox CRUD 完整闭环

**Segmentation 全链路 (5):**
 6. test_e2e_segmentation_create_dataset    创建 segmentation 数据集 + 类别
 7. test_e2e_segmentation_mask_upload       mask 上传 + 像素统计
 8. test_e2e_segmentation_voc_export        VOC-seg zip 目录布局完整
 9. test_e2e_segmentation_coco_export       COCO-seg JSON 完整字段
10. test_e2e_segmentation_mask_crud         mask CRUD 完整闭环

**通用约束:**
 - 复用 conftest 的 client / auth_headers / temp_upload_dir
 - 全部基于 pytest-asyncio + AsyncClient, 不依赖真实 Celery worker
 - 端到端验证: 数据集 → 图像 → 标注 → 导出 整条链路
"""
import io
import json
import zipfile

import pytest
from PIL import Image as PILImage


# ============== 工具 ==============

def _make_png_bytes(mode: str = "RGB", w: int = 48, h: int = 48,
                    color=None) -> bytes:
    """生成 w x h 的 PNG bytes (RGB / P / L 三种 mode 都支持)"""
    if color is None:
        color = (200, 100, 50)
    img = PILImage.new(mode, (w, h), color=color)
    if mode == "P":
        palette = [0] * 768
        palette[0:3] = [0, 0, 0]
        palette[3:6] = [255, 255, 255]
        palette[6:9] = [255, 0, 0]
        img.putpalette(palette)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_e2e_dataset(
    client, auth_headers, task_type: str, n_images: int = 2,
    n_categories: int = 2, image_w: int = 80, image_h: int = 60,
):
    """创建数据集 + 类别 + 图像 (S8 复用工具)"""
    # 1) dataset
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": f"e2e_{task_type}", "task_type": task_type},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]

    # 2) categories
    cat_ids = []
    for i in range(n_categories):
        r = await client.post(
            f"/api/datasets/{ds_id}/categories", headers=auth_headers,
            json={"name": f"cls_{i}"},
        )
        assert r.status_code in (200, 201), r.text
        cat_ids.append(r.json()["id"])

    # 3) 上传 n_images 张图 (颜色不同避免 hash 去重)
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

    # 4) 列表拿 id
    r = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size={n_images}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    img_ids = [it["id"] for it in items[:n_images]]
    return ds_id, cat_ids, img_ids


# ============== Detection E2E: 5 条 ==============

@pytest.mark.asyncio
async def test_e2e_detection_create_dataset(client, auth_headers):
    """E2E 1: 创建 detection 数据集 + 类别"""
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "e2e_det_create", "task_type": "detection",
              "description": "E2E detection dataset"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]
    assert r.json()["task_type"] == "detection"

    # 加类别
    r = await client.post(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers,
        json={"name": "person"},
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["name"] == "person"

    # 列表里能查到
    r = await client.get(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    cats = r.json().get("items", r.json())
    assert any(c["name"] == "person" for c in cats)


@pytest.mark.asyncio
async def test_e2e_detection_upload_bbox(
    client, auth_headers, temp_upload_dir,
):
    """E2E 2: detection 数据集 + 上传 bbox 标注"""
    ds_id, cat_ids, img_ids = await _make_e2e_dataset(
        client, auth_headers, task_type="detection",
        n_images=1, n_categories=1,
    )

    # 写 1 条 bbox
    r = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": img_ids[0]},
        headers=auth_headers,
        json={
            "x_min": 0.1, "y_min": 0.1,
            "x_max": 0.6, "y_max": 0.6,
            "category_id": cat_ids[0],
            "confidence": 0.92,
        },
    )
    assert r.status_code in (200, 201), r.text
    bbox_id = r.json()["id"]

    # 列表能查到
    r = await client.get(
        f"/api/detection/annotations/{img_ids[0]}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json().get("items", r.json())
    assert len(items) == 1
    assert items[0]["id"] == bbox_id


@pytest.mark.asyncio
async def test_e2e_detection_yolo_export(
    client, auth_headers, temp_upload_dir,
):
    """E2E 3: detection 数据集 YOLO-det 导出完整结构"""
    ds_id, cat_ids, img_ids = await _make_e2e_dataset(
        client, auth_headers, task_type="detection",
        n_images=2, n_categories=1,
    )
    # 给每图写 bbox
    for img_id in img_ids:
        r = await client.post(
            "/api/detection/annotations/save",
            params={"image_id": img_id},
            headers=auth_headers,
            json={
                "x_min": 0.2, "y_min": 0.2,
                "x_max": 0.5, "y_max": 0.5,
                "category_id": cat_ids[0],
            },
        )
        assert r.status_code in (200, 201), r.text

    r = await client.get(
        f"/api/export/yolo-det/{ds_id}?val_ratio=0.5",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert "data.yaml" in names
    # 至少 2 张图 + 2 个 label
    train_imgs = [n for n in names if n.startswith("images/train/")]
    val_imgs = [n for n in names if n.startswith("images/val/")]
    assert len(train_imgs) + len(val_imgs) == 2
    # data.yaml 含 nc + names
    yaml_text = zf.read("data.yaml").decode("utf-8")
    assert "nc: 1" in yaml_text
    assert "cls_0" in yaml_text


@pytest.mark.asyncio
async def test_e2e_detection_coco_export(
    client, auth_headers, temp_upload_dir,
):
    """E2E 4: detection 数据集 COCO-det 导出完整字段"""
    ds_id, cat_ids, img_ids = await _make_e2e_dataset(
        client, auth_headers, task_type="detection",
        n_images=2, n_categories=1, image_w=100, image_h=80,
    )
    for img_id in img_ids:
        r = await client.post(
            "/api/detection/annotations/save",
            params={"image_id": img_id},
            headers=auth_headers,
            json={
                "x_min": 0.1, "y_min": 0.1,
                "x_max": 0.5, "y_max": 0.5,
                "category_id": cat_ids[0],
            },
        )
        assert r.status_code in (200, 201), r.text

    r = await client.get(
        f"/api/export/coco-det/{ds_id}?include_pending=true",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["info"]["task_type"] == "detection"
    assert len(data["images"]) == 2
    assert len(data["categories"]) == 1
    assert len(data["annotations"]) == 2
    # bbox 像素
    a0 = data["annotations"][0]
    x, y, w, h = a0["bbox"]
    assert (x, y, w, h) == (10.0, 8.0, 40.0, 32.0)
    assert a0["area"] == 40.0 * 32.0


@pytest.mark.asyncio
async def test_e2e_detection_bbox_crud(
    client, auth_headers, temp_upload_dir,
):
    """E2E 5: bbox 完整 CRUD 闭环 (create→read→update→delete)"""
    ds_id, cat_ids, img_ids = await _make_e2e_dataset(
        client, auth_headers, task_type="detection",
        n_images=1, n_categories=2,
    )

    # Create
    r = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": img_ids[0]},
        headers=auth_headers,
        json={
            "x_min": 0.1, "y_min": 0.1,
            "x_max": 0.4, "y_max": 0.4,
            "category_id": cat_ids[0],
        },
    )
    assert r.status_code in (200, 201), r.text
    bbox_id = r.json()["id"]

    # Read
    r = await client.get(
        f"/api/detection/annotations/{img_ids[0]}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json().get("items", r.json())
    assert any(b["id"] == bbox_id for b in items)

    # Delete
    r = await client.delete(
        f"/api/detection/annotations/{bbox_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    # 复核: list 为空
    r = await client.get(
        f"/api/detection/annotations/{img_ids[0]}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json().get("items", r.json())
    assert len(items) == 0


# ============== Segmentation E2E: 5 条 ==============

@pytest.mark.asyncio
async def test_e2e_segmentation_create_dataset(client, auth_headers):
    """E2E 6: 创建 segmentation 数据集 + 类别"""
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "e2e_seg_create", "task_type": "segmentation",
              "description": "E2E segmentation dataset"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]
    assert r.json()["task_type"] == "segmentation"

    # 多类
    for name in ["background", "object", "edge"]:
        r = await client.post(
            f"/api/datasets/{ds_id}/categories", headers=auth_headers,
            json={"name": name},
        )
        assert r.status_code in (200, 201), r.text

    # 列表验证
    r = await client.get(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers,
    )
    cats = r.json().get("items", r.json())
    assert len(cats) == 3


@pytest.mark.asyncio
async def test_e2e_segmentation_mask_upload(
    client, auth_headers, temp_upload_dir,
):
    """E2E 7: segmentation mask 上传 + 像素统计"""
    ds_id, cat_ids, img_ids = await _make_e2e_dataset(
        client, auth_headers, task_type="segmentation",
        n_images=1, n_categories=2,
    )

    # 上传 P-mode 索引 PNG (bg=0, fg=1)
    png_bytes = _make_png_bytes("P", w=32, h=32, color=0)
    # 画一个 fg 区域
    img = PILImage.open(io.BytesIO(png_bytes)).convert("P")
    palette = [0] * 768
    palette[0:3] = [0, 0, 0]
    palette[3:6] = [255, 255, 255]
    img.putpalette(palette)
    for y in range(0, 16):
        for x in range(0, 16):
            img.putpixel((x, y), 1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

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
    # 像素统计
    counts = body["category_pixel_counts"]
    assert "0" in counts
    assert "1" in counts
    assert counts["0"] + counts["1"] == 32 * 32


@pytest.mark.asyncio
async def test_e2e_segmentation_voc_export(
    client, auth_headers, temp_upload_dir,
):
    """E2E 8: segmentation 数据集 VOC-seg 导出完整结构"""
    ds_id, cat_ids, img_ids = await _make_e2e_dataset(
        client, auth_headers, task_type="segmentation",
        n_images=2, n_categories=2,
    )
    # 每图上传 mask
    for img_id in img_ids:
        png_bytes = _make_png_bytes("L", w=24, h=24, color=0)
        r = await client.post(
            f"/api/segmentation/masks/upload/{img_id}",
            headers=auth_headers,
            files={"file": ("mask.png", png_bytes, "image/png")},
        )
        assert r.status_code == 200, r.text

    r = await client.get(
        f"/api/export/voc-seg/{ds_id}?include_pending=true&val_ratio=0.5",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())

    # VOC 标准目录布局
    jpegs = [n for n in names if n.startswith("JPEGImages/") and n.endswith(".jpg")]
    segs = [n for n in names if n.startswith("SegmentationClass/")]
    assert len(jpegs) >= 2
    assert len(segs) >= 2
    # ImageSets/Segmentation
    assert "ImageSets/Segmentation/train.txt" in names
    assert "ImageSets/Segmentation/val.txt" in names
    assert "ImageSets/Segmentation/trainval.txt" in names
    # label_colors.txt
    palette = zf.read("label_colors.txt").decode("utf-8")
    assert "background" in palette
    assert "cls_0" in palette


@pytest.mark.asyncio
async def test_e2e_segmentation_coco_export(
    client, auth_headers, temp_upload_dir,
):
    """E2E 9: segmentation 数据集 COCO-seg 导出完整字段"""
    ds_id, cat_ids, img_ids = await _make_e2e_dataset(
        client, auth_headers, task_type="segmentation",
        n_images=1, n_categories=2,
    )
    # 上传 mask (画一个 fg 区域, 像素值 1)
    png_bytes = _make_png_bytes("P", w=32, h=32, color=0)
    img = PILImage.open(io.BytesIO(png_bytes)).convert("P")
    palette = [0] * 768
    palette[0:3] = [0, 0, 0]
    palette[3:6] = [255, 255, 255]
    img.putpalette(palette)
    for y in range(0, 16):
        for x in range(0, 16):
            img.putpixel((x, y), 1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    r = await client.post(
        f"/api/segmentation/masks/upload/{img_ids[0]}",
        headers=auth_headers,
        files={"file": ("mask.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text

    r = await client.get(
        f"/api/export/coco-seg/{ds_id}?include_pending=true",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["info"]["task_type"] == "segmentation"
    assert len(data["images"]) == 1
    assert len(data["categories"]) == 2
    # 至少 1 个 annotation (像素值 1 的连通域)
    assert len(data["annotations"]) >= 1
    a0 = data["annotations"][0]
    assert "segmentation" in a0
    assert "bbox" in a0
    assert "area" in a0
    # polygon 4 顶点
    poly = a0["segmentation"][0]
    assert len(poly) == 8


@pytest.mark.asyncio
async def test_e2e_segmentation_mask_crud(
    client, auth_headers, temp_upload_dir,
):
    """E2E 10: segmentation mask 完整 CRUD 闭环 (upload→get→delete)"""
    ds_id, cat_ids, img_ids = await _make_e2e_dataset(
        client, auth_headers, task_type="segmentation",
        n_images=1, n_categories=2,
    )

    # Upload
    png_bytes = _make_png_bytes("L", w=16, h=16, color=0)
    r = await client.post(
        f"/api/segmentation/masks/upload/{img_ids[0]}",
        headers=auth_headers,
        files={"file": ("mask.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    mask_id = r.json()["id"]

    # Get
    r = await client.get(
        f"/api/segmentation/masks/{img_ids[0]}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_exists"] is True
    assert body["width"] == 16
    assert body["height"] == 16

    # Delete
    r = await client.delete(
        f"/api/segmentation/masks/{mask_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    assert r.json()["file_deleted"] is True

    # 复核: GET 应返回 200 + file_exists:False (v2.5.0-s12.7 契约: mask 不存在不抛 404)
    r = await client.get(
        f"/api/segmentation/masks/{img_ids[0]}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] is None
    assert body["file_exists"] is False
