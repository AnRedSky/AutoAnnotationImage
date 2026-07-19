"""
v2.0.0 S2 测试: 目标检测 bbox CRUD + Service (IoU/NMS) + 级联
============================================================

覆盖 8 条用例 (按 S2 验收标准):
1. test_bbox_create_and_get              单条 BBox 保存 + 拉取
2. test_bbox_create_invalid_coord        非法坐标 (越界/宽高 0) 拒绝
3. test_bbox_create_wrong_task_type      classification 图拒写 bbox
4. test_bbox_replace_clears_old          replace 语义: 全量替换
5. test_bbox_delete                      单条删除
6. test_bbox_iou_nms_pure                Service 纯函数: IoU + NMS
7. test_bbox_normalized_pixel_convert    Service: 归一化↔像素 互转
8. test_bbox_cascade_on_image_delete     Image 删除时 CASCADE 清 bbox
"""
import io
import pytest
from PIL import Image
from sqlalchemy import select

from app.models import BBoxAnnotation
from app.services.bbox_service import (
    BBox, iou, nms, class_wise_nms,
    normalized_to_pixels, pixels_to_normalized,
    validate_normalized_bbox, bbox_to_yolo_line, yolo_line_to_bbox,
)


def _make_png_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), color=(180, 120, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_detection_dataset_with_image(client, auth_headers, name="det_ds"):
    """辅助: 创建 detection 数据集 + 1 张图 + 1 个类别, 返回 (ds_id, image_id, cat_id)"""
    # 1) 创建 detection 数据集
    resp = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": name, "task_type": "detection"},
    )
    assert resp.status_code in (200, 201), f"dataset create failed: {resp.text}"
    ds_id = resp.json()["id"]

    # 2) 添加一个类别
    cat_resp = await client.post(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers,
        json={"name": "cat"},
    )
    assert cat_resp.status_code in (200, 201), f"category create failed: {cat_resp.text}"
    cat_id = cat_resp.json()["id"]

    # 3) 上传一张图
    up = await client.post(
        f"/api/images/upload/{ds_id}", headers=auth_headers,
        files=[("files", ("a.png", _make_png_bytes(), "image/png"))],
    )
    assert up.status_code in (200, 201), f"upload failed: {up.text}"
    assert up.json().get("uploaded", 0) >= 1, f"upload didn't accept: {up.text}"

    # 4) list 拿 image_id
    lst = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size=5", headers=auth_headers
    )
    items = lst.json().get("items", [])
    assert items, f"no image listed: {lst.text}"
    image_id = items[0]["id"]

    return ds_id, image_id, cat_id


# ============== 1. CRUD: save + get ==============

@pytest.mark.asyncio
async def test_bbox_create_and_get(client, auth_headers, temp_upload_dir):
    """单条 BBox 写入 + 拉回字段一致"""
    _, image_id, cat_id = await _make_detection_dataset_with_image(
        client, auth_headers
    )
    # 写入
    resp = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": image_id},
        headers=auth_headers,
        json={
            "x_min": 0.1, "y_min": 0.2, "x_max": 0.5, "y_max": 0.6,
            "category_id": cat_id, "confidence": 0.95,
            "source": "ai",
        },
    )
    assert resp.status_code in (200, 201), f"save failed: {resp.text}"
    bb = resp.json()
    assert bb["image_id"] == image_id
    assert bb["category_id"] == cat_id
    assert bb["x_min"] == 0.1
    assert bb["y_max"] == 0.6
    assert bb["source"] == "ai"
    assert bb["confidence"] == 0.95
    bb_id = bb["id"]

    # 拉取
    lst = await client.get(
        f"/api/detection/annotations/{image_id}", headers=auth_headers
    )
    assert lst.status_code == 200
    assert lst.json()["image_id"] == image_id
    assert len(lst.json()["items"]) == 1
    assert lst.json()["items"][0]["id"] == bb_id


# ============== 2. 坐标校验 ==============

@pytest.mark.asyncio
async def test_bbox_create_invalid_coord(client, auth_headers, temp_upload_dir):
    """越界 / 宽高为 0 全部拒绝

    区分两种拒绝码:
    - 422: Pydantic schema 校验 (Field ge/le 边界)
    - 400: 业务校验 (x_max > x_min 等结构约束, schema 通过但业务层拒)
    """
    _, image_id, cat_id = await _make_detection_dataset_with_image(
        client, auth_headers
    )
    # 1) 越界 x_max > 1 → Pydantic 422 (Field le=1)
    r1 = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": image_id},
        headers=auth_headers,
        json={
            "x_min": 0.1, "y_min": 0.2, "x_max": 1.5, "y_max": 0.6,
            "category_id": cat_id,
        },
    )
    assert r1.status_code == 422, f"x_max>1 应被 Pydantic 拒 (422), 实际: {r1.status_code}"

    # 2) x_max == x_min (宽 0) → 业务层 400 (schema 通过, route 拒)
    r2 = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": image_id},
        headers=auth_headers,
        json={
            "x_min": 0.3, "y_min": 0.2, "x_max": 0.3, "y_max": 0.6,
            "category_id": cat_id,
        },
    )
    assert r2.status_code == 400, f"x_max==x_min 应被业务层拒 (400), 实际: {r2.status_code}"

    # 3) 负值 x_min → Pydantic 422 (Field ge=0)
    r3 = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": image_id},
        headers=auth_headers,
        json={
            "x_min": -0.1, "y_min": 0.2, "x_max": 0.5, "y_max": 0.6,
            "category_id": cat_id,
        },
    )
    assert r3.status_code == 422, f"x_min<0 应被 Pydantic 拒 (422), 实际: {r3.status_code}"


# ============== 3. 任务类型守卫 ==============

@pytest.mark.asyncio
async def test_bbox_create_wrong_task_type(
    client, auth_headers, temp_upload_dir
):
    """classification 图拒写 bbox"""
    # 1) 创建一个 classification 数据集 + 1 张图
    resp = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "cls_ds", "task_type": "classification"},
    )
    ds_id = resp.json()["id"]
    await client.post(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers,
        json={"name": "x"},
    )
    up = await client.post(
        f"/api/images/upload/{ds_id}", headers=auth_headers,
        files=[("files", ("a.png", _make_png_bytes(), "image/png"))],
    )
    lst = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size=5", headers=auth_headers
    )
    image_id = lst.json()["items"][0]["id"]

    # 2) 尝试在 classification 图上写 bbox → 400
    r = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": image_id},
        headers=auth_headers,
        json={
            "x_min": 0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.5,
            "category_id": None,
        },
    )
    assert r.status_code == 400, f"classification 图应被拒, 实际: {r.status_code}"
    assert "detection" in r.text


# ============== 4. replace 语义 ==============

@pytest.mark.asyncio
async def test_bbox_replace_clears_old(client, auth_headers, temp_upload_dir):
    """replace 全量替换: 旧 bbox 全部删除, 新 bbox 全部写入"""
    _, image_id, cat_id = await _make_detection_dataset_with_image(
        client, auth_headers
    )
    # 1) 写入 3 条旧 bbox
    for i in range(3):
        r = await client.post(
            "/api/detection/annotations/save",
            params={"image_id": image_id},
            headers=auth_headers,
            json={
                "x_min": 0.1 * i, "y_min": 0.1, "x_max": 0.1 * i + 0.05, "y_max": 0.2,
                "category_id": cat_id, "source": "human",
            },
        )
        assert r.status_code in (200, 201), r.text
    lst = await client.get(
        f"/api/detection/annotations/{image_id}", headers=auth_headers
    )
    assert len(lst.json()["items"]) == 3

    # 2) replace 为 2 条新 bbox
    new_items = [
        {
            "x_min": 0.2, "y_min": 0.2, "x_max": 0.4, "y_max": 0.5,
            "category_id": cat_id, "source": "human",
        },
        {
            "x_min": 0.5, "y_min": 0.5, "x_max": 0.8, "y_max": 0.9,
            "category_id": cat_id, "source": "human",
        },
    ]
    r = await client.post(
        "/api/detection/annotations/replace",
        params={"image_id": image_id},
        headers=auth_headers, json=new_items,
    )
    assert r.status_code in (200, 201), r.text
    items = r.json()["items"]
    assert len(items) == 2
    # 旧 3 条已全部删除
    for it in items:
        assert it["x_min"] in (0.2, 0.5)


# ============== 5. 单条删除 ==============

@pytest.mark.asyncio
async def test_bbox_delete(client, auth_headers, temp_upload_dir):
    """单条 BBox 删除"""
    _, image_id, cat_id = await _make_detection_dataset_with_image(
        client, auth_headers
    )
    r = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": image_id},
        headers=auth_headers,
        json={
            "x_min": 0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.5,
            "category_id": cat_id,
        },
    )
    bb_id = r.json()["id"]

    d = await client.delete(
        f"/api/detection/annotations/{bb_id}", headers=auth_headers
    )
    assert d.status_code == 200, d.text
    assert d.json()["deleted_id"] == bb_id

    # 再查: 该图已无 bbox
    lst = await client.get(
        f"/api/detection/annotations/{image_id}", headers=auth_headers
    )
    assert len(lst.json()["items"]) == 0

    # 再删 → 404
    d2 = await client.delete(
        f"/api/detection/annotations/{bb_id}", headers=auth_headers
    )
    assert d2.status_code == 404


# ============== 6. Service 纯函数: IoU + NMS ==============

def test_bbox_iou_nms_pure():
    """纯函数单元测试: 无 IO, 无 DB"""
    a = BBox(0.0, 0.0, 0.4, 0.4, confidence=0.9)
    b = BBox(0.2, 0.2, 0.6, 0.6, confidence=0.8)
    # 相交面积: 0.2*0.2 = 0.04
    # a 面积: 0.16, b 面积: 0.16, union: 0.28
    # IoU: 0.04/0.28 ≈ 0.1429
    expected_iou = 0.04 / 0.28
    assert abs(iou(a, b) - expected_iou) < 1e-6, f"IoU 计算错误: {iou(a, b)}"

    # 完全重合 → IoU = 1.0
    c = BBox(0.0, 0.0, 0.4, 0.4)
    assert iou(a, c) == 1.0

    # 不相交 → IoU = 0
    d = BBox(0.5, 0.5, 0.9, 0.9)
    assert iou(a, d) == 0.0

    # NMS: 3 个高度重叠的框, 只保留 confidence 最高的
    boxes = [
        BBox(0.0, 0.0, 0.4, 0.4, confidence=0.7),
        BBox(0.05, 0.05, 0.45, 0.45, confidence=0.95),  # 最高
        BBox(0.02, 0.02, 0.42, 0.42, confidence=0.6),
    ]
    kept = nms(boxes, iou_threshold=0.5)
    assert len(kept) == 1
    assert kept[0].confidence == 0.95

    # class_wise_nms: 不同类别互不抑制
    boxes2 = [
        BBox(0.0, 0.0, 0.4, 0.4, confidence=0.5, category_id=1),
        BBox(0.0, 0.0, 0.4, 0.4, confidence=0.9, category_id=2),  # 跨类别
    ]
    kept2 = class_wise_nms(boxes2, iou_threshold=0.5)
    assert len(kept2) == 2  # 不同类别都保留


# ============== 7. 坐标转换 ==============

def test_bbox_normalized_pixel_convert():
    """归一化 ↔ 像素 互转 + 边界"""
    # 1) 归一化 → 像素 (640x480)
    x1, y1, x2, y2 = normalized_to_pixels(0.0, 0.0, 0.5, 0.5, 640, 480)
    assert (x1, y1, x2, y2) == (0, 0, 320, 240)

    # 2) 像素 → 归一化 → 像素 (往返一致)
    nx1, ny1, nx2, ny2 = pixels_to_normalized(100, 50, 300, 200, 640, 480)
    bx1, by1, bx2, by2 = normalized_to_pixels(nx1, ny1, nx2, ny2, 640, 480)
    assert (bx1, by1, bx2, by2) == (100, 50, 300, 200)

    # 3) 越界抛错
    with pytest.raises(ValueError):
        normalized_to_pixels(-0.1, 0.0, 0.5, 0.5, 640, 480)
    # 合法坐标: 不抛
    validate_normalized_bbox(0.0, 0.0, 0.5, 0.5)
    # 非法 (x_max == x_min): 抛
    with pytest.raises(ValueError):
        validate_normalized_bbox(0.5, 0.0, 0.5, 0.5)

    # 4) YOLO 互转
    bb = BBox(0.1, 0.2, 0.5, 0.6, category_id=0)
    line = bbox_to_yolo_line(bb, class_index=0)
    # cx=0.3, cy=0.4, w=0.4, h=0.4
    assert line.startswith("0 ")
    bb2 = yolo_line_to_bbox(line)
    assert abs(bb2.x_min - 0.1) < 1e-6
    assert abs(bb2.y_min - 0.2) < 1e-6
    assert abs(bb2.x_max - 0.5) < 1e-6
    assert abs(bb2.y_max - 0.6) < 1e-6


# ============== 8. Image 删除时 CASCADE ==============

@pytest.mark.asyncio
async def test_bbox_cascade_on_image_delete(
    client, auth_headers, temp_upload_dir, db_session
):
    """删 Image 时, BBoxAnnotation 自动级联删除 (ORM cascade='all, delete-orphan' + ondelete CASCADE)"""
    _, image_id, cat_id = await _make_detection_dataset_with_image(
        client, auth_headers
    )
    # 写 2 条 bbox
    for i in range(2):
        r = await client.post(
            "/api/detection/annotations/save",
            params={"image_id": image_id},
            headers=auth_headers,
            json={
                "x_min": 0.1 * i, "y_min": 0.1, "x_max": 0.2, "y_max": 0.3,
                "category_id": cat_id,
            },
        )
        assert r.status_code in (200, 201), r.text
    # 验证 2 条已落库
    stmt = select(BBoxAnnotation).where(BBoxAnnotation.image_id == image_id)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 2

    # 删图
    d = await client.delete(f"/api/images/{image_id}", headers=auth_headers)
    assert d.status_code == 200, d.text

    # 验证 bbox 也被清
    rows_after = (await db_session.execute(stmt)).scalars().all()
    assert len(rows_after) == 0, f"级联失效, 仍有 {len(rows_after)} 条 bbox 残留"
