"""
v2.0.0 S4 测试: 目标检测导出 (YOLO-det zip / COCO-det JSON) + 激活
================================================================

覆盖 6 条用例 (S4 验收):

导出 (4):
 1. test_yolo_det_export_404             dataset 不存在
 2. test_yolo_det_export_wrong_type      classification 数据集 -> 400
 3. test_yolo_det_export_zip_structure   zip 含 data.yaml + images/* + labels/*
 4. test_coco_det_export_json_fields     JSON 字段: images/categories/annotations/bbox

激活 (2):
 5. test_activate_detection_model_ok     detection mv -> 200, is_active=True
 6. test_activate_classification_400     classification mv -> 400
"""
import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image as PILImage

from app.models import ModelVersion
from app.common.enums import TaskType


# ============== 工具 ==============

def _make_png_bytes(w: int, h: int, color) -> bytes:
    """生成 w x h 纯色 PNG (返回 bytes)"""
    img = PILImage.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_detection_dataset(
    client, auth_headers, n_images: int = 2, with_bbox: bool = True,
    image_w: int = 100, image_h: int = 80,
):
    """
    创建 detection 数据集 + n_images 张图 + 1 类
    返回 (ds_id, cat_id, img_ids)
    """
    # 1) 创建数据集
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "ds_det", "task_type": "detection"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]

    # 2) 创建类别
    r = await client.post(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers,
        json={"name": "obj"},
    )
    assert r.status_code in (200, 201), r.text
    cat_id = r.json()["id"]

    # 3) 多文件上传 (每图不同颜色避免 hash 去重)
    files = [
        ("files", (f"img_{i}.png",
                   _make_png_bytes(image_w, image_h,
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

    # 4) 列出图片拿 id
    r = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size={n_images}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= n_images, r.text
    img_ids = [it["id"] for it in items[:n_images]]

    # 5) 可选给每张图写 1 条 bbox
    if with_bbox:
        for img_id in img_ids:
            r = await client.post(
                "/api/detection/annotations/save",
                params={"image_id": img_id},
                headers=auth_headers,
                json={
                    "x_min": 0.1, "y_min": 0.1,
                    "x_max": 0.5, "y_max": 0.5,
                    "category_id": cat_id,
                },
            )
            assert r.status_code in (200, 201), r.text

    return ds_id, cat_id, img_ids


# ============== 导出: 4 条 ==============

@pytest.mark.asyncio
async def test_yolo_det_export_404(client, auth_headers):
    """dataset 不存在 -> 404"""
    r = await client.get("/api/export/yolo-det/99999", headers=auth_headers)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_yolo_det_export_wrong_type(client, auth_headers):
    """classification 数据集 -> 400 (不能当检测导出)"""
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "cls_only", "task_type": "classification"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]

    r = await client.get(f"/api/export/yolo-det/{ds_id}", headers=auth_headers)
    assert r.status_code == 400, r.text
    assert "detection" in r.text


@pytest.mark.asyncio
async def test_yolo_det_export_zip_structure(
    client, auth_headers, temp_upload_dir,
):
    """检测数据集 -> zip 含 data.yaml + images/ + labels/"""
    ds_id, cat_id, img_ids = await _make_detection_dataset(
        client, auth_headers, n_images=2, with_bbox=True,
    )
    assert len(img_ids) == 2

    r = await client.get(
        f"/api/export/yolo-det/{ds_id}", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"

    # 解压 zip 验证结构
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert "data.yaml" in names
    train_imgs = [n for n in names if n.startswith("images/train/")]
    val_imgs = [n for n in names if n.startswith("images/val/")]
    train_lbls = [n for n in names if n.startswith("labels/train/")]
    val_lbls = [n for n in names if n.startswith("labels/val/")]
    assert len(train_imgs) + len(val_imgs) == 2, names
    assert len(train_lbls) + len(val_lbls) == 2, names

    # data.yaml 含 nc + names
    yaml_text = zf.read("data.yaml").decode("utf-8")
    assert "nc: 1" in yaml_text
    assert "obj" in yaml_text
    # 校验 header
    assert r.headers.get("x-train-count") is not None
    assert r.headers.get("x-val-count") is not None


@pytest.mark.asyncio
async def test_coco_det_export_json_fields(
    client, auth_headers, temp_upload_dir,
):
    """COCO-det JSON 字段完整: images / categories / annotations / bbox"""
    ds_id, cat_id, img_ids = await _make_detection_dataset(
        client, auth_headers, n_images=2, with_bbox=True,
        image_w=100, image_h=80,
    )
    r = await client.get(
        f"/api/export/coco-det/{ds_id}?include_pending=true",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # 1) top-level 字段
    for k in ("info", "images", "categories", "annotations"):
        assert k in data, f"missing {k}"
    # 2) images: 2 张
    assert len(data["images"]) == 2
    img0 = data["images"][0]
    assert {"id", "file_name", "width", "height"} <= img0.keys()
    assert img0["width"] == 100
    assert img0["height"] == 80
    # 3) categories: 1 类
    assert len(data["categories"]) == 1
    assert data["categories"][0]["name"] == "obj"
    # 4) annotations: 2 条 (每图 1 框)
    assert len(data["annotations"]) == 2
    a0 = data["annotations"][0]
    assert {"id", "image_id", "category_id", "bbox", "area", "iscrowd"} <= a0.keys()
    # bbox = [x, y, w, h] 像素
    x, y, w, h = a0["bbox"]
    # 归一化 [0.1, 0.1, 0.5, 0.5] -> 像素 [10, 8, 40, 32]
    assert (x, y, w, h) == (10.0, 8.0, 40.0, 32.0), a0["bbox"]
    assert a0["area"] == 40.0 * 32.0
    assert a0["iscrowd"] == 0


# ============== 激活: 2 条 ==============

@pytest.mark.asyncio
async def test_activate_detection_model_ok(
    client, auth_headers, db_session, temp_upload_dir,
):
    """detection mv -> 激活成功, is_active=True"""
    ds_id, cat_id, img_ids = await _make_detection_dataset(
        client, auth_headers, n_images=1, with_bbox=True,
    )

    # 直接造一个 detection ModelVersion (不跑真实训练)
    mv = ModelVersion(
        name="det_mv_test",
        base_model="yolov8n",
        dataset_id=ds_id,
        task_type=TaskType.DETECTION.value,
        num_classes=1,
        file_path="/tmp/fake.pt",
        is_active=False,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)
    mv_id = mv.id

    r = await client.post(
        f"/api/detection/models/{mv_id}/activate",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["is_active"] is True
    assert body["task_type"] == "detection"

    # 复核: db 里的 is_active 已置 True
    await db_session.refresh(mv)
    assert mv.is_active is True


@pytest.mark.asyncio
async def test_activate_classification_400(
    client, auth_headers, db_session,
):
    """classification mv -> 400 (不允许在 detection 端点激活)"""
    mv = ModelVersion(
        name="cls_mv_test",
        base_model="resnet18",
        dataset_id=None,
        task_type=TaskType.CLASSIFICATION.value,
        num_classes=2,
        file_path="/tmp/fake_cls.pt",
        is_active=False,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)

    r = await client.post(
        f"/api/detection/models/{mv.id}/activate",
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert "detection" in r.text
