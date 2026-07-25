"""
v2.0.0 S5.2 测试: 图像分割训练 + 自动标注
==========================================

覆盖 4 条用例 (S5.2 验收):
 1. test_seg_train_endpoint_creates_task     POST /train 启动 Celery (mock .delay)
 2. test_seg_train_wrong_task_type           classification 数据集 -> 400
 3. test_seg_train_redis_down_503            Redis ping 抛异常 -> 503
 4. test_seg_auto_annotate_validation        /auto-annotate 任务/模型校验

训练数据本身在 S5.1 test_segmentation_mask.py 已覆盖 (mask CRUD 链路),
此处专注训练链路 + 任务校验.
"""
import io
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image as PILImage

from app.models import ModelVersion
from app.common.enums import TaskType


# ============== 工具 ==============

def _make_png_bytes(w: int, h: int, color) -> bytes:
    img = PILImage.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_segmentation_dataset(
    client, auth_headers, n_images: int = 2, n_categories: int = 2,
    image_w: int = 64, image_h: int = 64,
):
    """构造一个带 mask 的 segmentation 数据集, 给 train/auto-annotate 用"""
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "ds_seg_train", "task_type": "segmentation"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]

    cat_ids = []
    for i in range(n_categories):
        r = await client.post(
            f"/api/datasets/{ds_id}/categories", headers=auth_headers,
            json={"name": f"cls_{i}"},
        )
        assert r.status_code in (200, 201), r.text
        cat_ids.append(r.json()["id"])

    # 多文件上传
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
    r = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size={n_images}",
        headers=auth_headers,
    )
    items = r.json()["items"]
    img_ids = [it["id"] for it in items[:n_images]]

    # 给每张图上 1 个 mask (P-mode 索引, 0/1 像素)
    for img_id in img_ids:
        mask = PILImage.new("P", (image_w, image_h), color=0)
        # 1/4 区域填 1
        for y in range(image_h // 2):
            for x in range(image_w // 2):
                mask.putpixel((x, y), 1)
        buf = io.BytesIO()
        mask.save(buf, format="PNG")
        r = await client.post(
            f"/api/segmentation/masks/upload/{img_id}",
            headers=auth_headers,
            files={"file": ("mask.png", buf.getvalue(), "image/png")},
        )
        assert r.status_code == 200, r.text

    return ds_id, cat_ids, img_ids


# ============== 训练链路: 3 条 ==============

@pytest.mark.asyncio
async def test_seg_train_endpoint_creates_task(
    client, auth_headers, temp_upload_dir,
):
    """POST /train -> 200, mock Celery .delay 拿到 task_id"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=2, n_categories=2,
    )

    mock_result = MagicMock()
    mock_result.id = "celery-task-id-1234"
    with patch(
        "app.workers.segmentation_tasks.train_segmentation_task.delay",
        return_value=mock_result,
    ) as mock_delay:
        r = await client.post(
            "/api/segmentation/train", headers=auth_headers,
            json={
                "dataset_id": ds_id,
                "backbone": "deeplabv3_resnet50",
                "epochs": 1,
                "batch_size": 2,
                "crop_size": 64,
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"] == "celery-task-id-1234"
    assert body["state"] == "PENDING"
    # mock 被调用, 参数对得上
    assert mock_delay.called
    call_kwargs = mock_delay.call_args.kwargs
    assert call_kwargs["dataset_id"] == ds_id
    assert call_kwargs["backbone"] == "deeplabv3_resnet50"
    assert call_kwargs["epochs"] == 1


@pytest.mark.asyncio
async def test_seg_train_wrong_task_type(
    client, auth_headers,
):
    """classification 数据集 -> 400, 不投递任务"""
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "cls_only_seg", "task_type": "classification"},
    )
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]

    r = await client.post(
        "/api/segmentation/train", headers=auth_headers,
        json={"dataset_id": ds_id, "epochs": 1},
    )
    assert r.status_code == 400, r.text
    assert "segmentation" in r.text


@pytest.mark.asyncio
async def test_seg_train_redis_down_503(
    client, auth_headers, temp_upload_dir,
):
    """Redis ping 抛异常 -> 503, 不投递任务"""
    ds_id, cat_ids, img_ids = await _make_segmentation_dataset(
        client, auth_headers, n_images=1, n_categories=2,
    )

    with patch(
        "app.database.redis.redis_client.ping",
        side_effect=ConnectionError("redis down"),
    ):
        r = await client.post(
            "/api/segmentation/train", headers=auth_headers,
            json={"dataset_id": ds_id, "epochs": 1},
        )
    assert r.status_code == 503, r.text
    assert "Redis" in r.text


# ============== 自动标注: 1 条 ==============

@pytest.mark.asyncio
async def test_seg_auto_annotate_validation(
    client, auth_headers, db_session, temp_upload_dir,
):
    """/auto-annotate: 任务/模型校验 (错 dataset / 错 mv / 缺 mv)"""
    # 1) classification 数据集 -> 400
    r = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "cls_for_seg_aa", "task_type": "classification"},
    )
    assert r.status_code in (200, 201), r.text
    cls_ds_id = r.json()["id"]

    r = await client.post(
        "/api/segmentation/auto-annotate",
        params={"dataset_id": cls_ds_id, "model_version_id": 999},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert "segmentation" in r.text

    # 2) 真实 segmentation 数据集 + 不存在的 mv -> 404
    ds_id, _, _ = await _make_segmentation_dataset(
        client, auth_headers, n_images=1, n_categories=2,
    )
    r = await client.post(
        "/api/segmentation/auto-annotate",
        params={"dataset_id": ds_id, "model_version_id": 99999},
        headers=auth_headers,
    )
    assert r.status_code == 404, r.text

    # 3) 真实 seg ds + classification mv (任务类型不匹配) -> 400
    mv_cls = ModelVersion(
        name="cls_mv_for_seg_test",
        base_model="resnet18",
        dataset_id=None,
        task_type=TaskType.CLASSIFICATION.value,
        num_classes=2,
        file_path="/tmp/fake_cls.pt",
        is_active=False,
    )
    db_session.add(mv_cls)
    await db_session.commit()
    await db_session.refresh(mv_cls)

    r = await client.post(
        "/api/segmentation/auto-annotate",
        params={"dataset_id": ds_id, "model_version_id": mv_cls.id},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert "segmentation" in r.text
