"""
v2.0.0 S6 测试: 图像分割导出 (VOC-seg zip / COCO-seg JSON)
===========================================================

覆盖 7 条用例 (S6 验收):

VOC-seg (4):
 1. test_voc_seg_export_404              dataset 不存在
 2. test_voc_seg_export_wrong_type       classification 数据集 -> 400
 3. test_voc_seg_export_zip_structure    zip 含 JPEGImages/SegmentationClass/ImageSets/label_colors
 4. test_voc_seg_export_include_pending  include_pending=True 时把未确认图也打包

COCO-seg (3):
 5. test_coco_seg_export_json_fields     JSON 字段完整: images/categories/annotations
 6. test_coco_seg_export_no_mask         无 mask 的图不生成 annotation
 7. test_coco_seg_export_wrong_type      detection 数据集 -> 400
"""
import io
import json
import zipfile

import pytest
from PIL import Image as PILImage


# ============== 工具 ==============

def _make_png_bytes(mode: str = "P", w: int = 32, h: int = 32,
                    bg_value: int = 0, fg_value: int = 1) -> bytes:
    """
    生成 mode 模式的 PNG bytes
    - mode='P': 索引图, 左上 1/4 区域填 fg_value
    - mode='L': 灰度图
    - mode='RGB': 原图
    """
    img = PILImage.new(mode, (w, h), color=bg_value)
    if mode == "P":
        palette = [0] * 768
        palette[0:3] = [0, 0, 0]
        palette[3:6] = [255, 255, 255]
        palette[6:9] = [255, 0, 0]
        img.putpalette(palette)
    for y in range(0, h // 2):
        for x in range(0, w // 2):
            img.putpixel((x, y), fg_value)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_segmentation_dataset(
    client, auth_headers, n_images: int = 2,
    image_w: int = 64, image_h: int = 64,
    n_categories: int = 2, with_masks: bool = True,
):
    """
    创建 segmentation 数据集 + n_images 张图 + n_categories 个类别
    可选给每张图上传 mask (P-mode, 像素值 0/1, 0=背景 1=类 cls_0)
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

    # 3) 上传原图 (不同颜色避免 hash 去重)
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

    # 5) 可选上传 mask
    if with_masks:
        for img_id in img_ids:
            png_bytes = _make_png_bytes("P", w=32, h=32, bg_value=0, fg_value=1)
            r = await client.post(
                f"/api/segmentation/masks/upload/{img_id}",
                headers=auth_headers,
                files={"file": ("mask.png", png_bytes, "image/png")},
            )
            assert r.status_code == 200, r.text

    return ds_id, cat_ids, img_ids


# ============== VOC-seg: 4 条 ==============

@pytest.mark.asyncio
async def test_voc_seg_export_404(client, auth_headers):
    """dataset 不存在 -> 404"""
    r = await client.get(
        "/api/export/voc-seg/99999", headers=auth_headers,
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_voc_seg_export_wrong_type(client, auth_headers):
    """classification 数据集 -> 400 (不能当分割导出)"""
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "cls_only", "task_type": "classification"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]

    r = await client.get(
        f"/api/export/voc-seg/{ds_id}", headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert "segmentation" in r.text


@pytest.mark.asyncio
async def test_voc_seg_export_zip_structure(
    client, auth_headers, temp_upload_dir,
):
    """segmentation 数据集 -> zip 含完整 VOC-seg 目录布局"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=2, with_masks=True,
    )
    assert len(img_ids) == 2

    r = await client.get(
        f"/api/export/voc-seg/{ds_id}?include_pending=true&val_ratio=0.5",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"

    # 解压 zip 验证结构
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())

    # 1) JPEGImages/*.jpg 至少 2 张
    jpegs = [n for n in names if n.startswith("JPEGImages/") and n.endswith(".jpg")]
    assert len(jpegs) >= 2, names

    # 2) SegmentationClass/*.png 至少 2 张
    seg_class = [n for n in names
                 if n.startswith("SegmentationClass/") and n.endswith(".png")]
    assert len(seg_class) >= 2, names

    # 3) ImageSets/Segmentation/{train,val,trainval}.txt
    assert "ImageSets/Segmentation/train.txt" in names
    assert "ImageSets/Segmentation/val.txt" in names
    assert "ImageSets/Segmentation/trainval.txt" in names

    # 4) label_colors.txt 含 background + 类别名
    palette_text = zf.read("label_colors.txt").decode("utf-8")
    assert "background" in palette_text
    for cid in cat_ids:
        # 类别名 cls_0 / cls_1
        idx = cat_ids.index(cid)
        assert f"cls_{idx}" in palette_text

    # 5) train/val 划分总和不超 2
    train_lines = zf.read("ImageSets/Segmentation/train.txt").decode("utf-8").strip().splitlines()
    val_lines = zf.read("ImageSets/Segmentation/val.txt").decode("utf-8").strip().splitlines()
    trainval_lines = zf.read("ImageSets/Segmentation/trainval.txt").decode("utf-8").strip().splitlines()
    assert len(train_lines) + len(val_lines) == len(trainval_lines)
    assert len(trainval_lines) == 2

    # 6) 校验 header
    assert r.headers.get("x-train-count") is not None
    assert r.headers.get("x-val-count") is not None


@pytest.mark.asyncio
async def test_voc_seg_export_include_pending(
    client, auth_headers, temp_upload_dir,
):
    """include_pending=True 时, 即便图未确认, 也会写 mask (前提是 mask 已上传)"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=2, with_masks=True,
    )
    # 此时图默认 status='pending', 不会出现在 train/val.txt 里
    r = await client.get(
        f"/api/export/voc-seg/{ds_id}?include_pending=true&val_ratio=0.5",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    # trainval 含 2 张
    trainval_lines = zf.read("ImageSets/Segmentation/trainval.txt").decode("utf-8").strip().splitlines()
    assert len(trainval_lines) == 2, trainval_lines

    # 默认 include_pending=False 时, 0 张
    r = await client.get(
        f"/api/export/voc-seg/{ds_id}?include_pending=false&val_ratio=0.5",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    trainval_lines = zf.read("ImageSets/Segmentation/trainval.txt").decode("utf-8").strip().splitlines()
    assert len(trainval_lines) == 0, trainval_lines


# ============== COCO-seg: 3 条 ==============

@pytest.mark.asyncio
async def test_coco_seg_export_json_fields(
    client, auth_headers, temp_upload_dir,
):
    """COCO-seg JSON 字段完整: images / categories / annotations / segmentation"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=2, n_categories=2, with_masks=True,
    )
    r = await client.get(
        f"/api/export/coco-seg/{ds_id}?include_pending=true",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()

    # 1) top-level
    for k in ("info", "images", "categories", "annotations"):
        assert k in data, f"missing {k}"
    assert data["info"]["task_type"] == "segmentation"

    # 2) images: 2 张
    assert len(data["images"]) == 2
    img0 = data["images"][0]
    assert {"id", "file_name", "width", "height"} <= img0.keys()

    # 3) categories: 2 个
    assert len(data["categories"]) == 2
    names = sorted([c["name"] for c in data["categories"]])
    assert names == ["cls_0", "cls_1"]

    # 4) annotations: 每图 1 个 (像素值 1 区域), 共 2 个
    assert len(data["annotations"]) == 2
    a0 = data["annotations"][0]
    for k in ("id", "image_id", "category_id",
              "segmentation", "bbox", "area", "iscrowd"):
        assert k in a0, f"missing {k}"
    # segmentation: 1 个 polygon (外接矩形 4 顶点)
    seg = a0["segmentation"]
    assert len(seg) == 1
    poly = seg[0]
    assert len(poly) == 8  # 4 个 (x, y) 顶点
    # bbox: [x, y, w, h] 与 polygon 顶点匹配
    bx, by, bw, bh = a0["bbox"]
    assert bx == poly[0]
    assert by == poly[1]
    assert bw == poly[2] - poly[0]
    assert bh == poly[5] - poly[1]
    # area = bbox 面积 (PIL P-mode 调色板优化后实际面积可能略小于 16*16,
    # 这里用 bbox 算得的等价值做断言, 容忍舍入误差)
    expected_area = bw * bh
    assert abs(a0["area"] - expected_area) < 1.0
    assert a0["iscrowd"] == 0
    assert a0["area"] > 0

    # 5) header
    assert r.headers.get("x-image-count") == "2"
    assert r.headers.get("x-annotation-count") == "2"


@pytest.mark.asyncio
async def test_coco_seg_export_no_mask(
    client, auth_headers, temp_upload_dir,
):
    """无 mask 的图不会生成 annotation"""
    # 创建数据集但不上传 mask
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=2, n_categories=2, with_masks=False,
    )
    r = await client.get(
        f"/api/export/coco-seg/{ds_id}?include_pending=true",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # images 仍然有 2 张, 但 annotations 为空
    assert len(data["images"]) == 2
    assert len(data["annotations"]) == 0


@pytest.mark.asyncio
async def test_coco_seg_export_wrong_type(client, auth_headers):
    """detection 数据集 -> 400 (不能当分割导出)"""
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "det_only", "task_type": "detection"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]

    r = await client.get(
        f"/api/export/coco-seg/{ds_id}", headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert "segmentation" in r.text
