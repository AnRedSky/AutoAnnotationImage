"""
v2.0.0 S3 测试: 目标检测训练/自动标注/API 端点
=================================================

覆盖 10 条用例 (S3 验收标准):

Service / 数据层 (不依赖 ultralytics/Celery):
 1. test_yolo_dataset_split_train_val        split_train_val 固定 seed 可复现
 2. test_yolo_dataset_annotations_to_lines   BBoxAnnotation → YOLO txt 行
 3. test_yolo_dataset_export_writes_yaml    export_yolo_dataset 写 data.yaml
 4. test_yolo_dataset_export_skips_missing  缺图/无 bbox 自动跳过

API 端点 (mock Celery .delay):
 5. test_train_endpoint_creates_task         POST /train 启动
 6. test_train_endpoint_wrong_task_type      classification 数据集拒
 7. test_train_endpoint_redis_down_503       Redis 不可达 → 503
 8. test_auto_annotate_endpoint_validation   /auto-annotate 任务/模型校验
 9. test_job_progress_endpoint_returns_state GET /jobs/{id}/progress
10. test_job_progress_endpoint_404           GET /jobs/{id}/progress 不存在
"""
import io
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from app.models import BBoxAnnotation, TrainingJob
from app.ml.detection.yolo_dataset import (
    split_train_val,
    annotations_to_yolo_lines,
    export_yolo_dataset,
)
from app.schemas.detection import BBoxCreate


# ============== 工具 ==============

def _make_png_bytes(w: int = 32, h: int = 32, color=(120, 100, 60)) -> bytes:
    img = PILImage.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_detection_ds_with_2_images(client, auth_headers, name="ds_train"):
    """辅助: 创建 detection 数据集 + 2 张图 (内容不同, 避免去重) + 1 类"""
    resp = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": name, "task_type": "detection"},
    )
    assert resp.status_code in (200, 201), resp.text
    ds_id = resp.json()["id"]
    cat = await client.post(
        f"/api/datasets/{ds_id}/categories", headers=auth_headers,
        json={"name": "obj"},
    )
    cat_id = cat.json()["id"]
    # 关键: 2 张图必须用不同尺寸/颜色, 避免系统按文件 hash 去重
    up = await client.post(
        f"/api/images/upload/{ds_id}", headers=auth_headers,
        files=[
            ("files", ("img_0.png", _make_png_bytes(32, 32, (120, 100, 60)), "image/png")),
            ("files", ("img_1.png", _make_png_bytes(48, 48, (60, 200, 130)), "image/png")),
        ],
    )
    assert up.status_code in (200, 201), up.text
    assert up.json().get("uploaded", 0) >= 2, f"upload fail: {up.text}"
    lst = await client.get(
        f"/api/images/list/{ds_id}?page=1&page_size=10", headers=auth_headers,
    )
    items = lst.json()["items"]
    assert len(items) >= 2, f"list only {len(items)}: {lst.text}"
    return ds_id, cat_id, [i["id"] for i in items]


# ============== 1. split_train_val ==============

def test_yolo_dataset_split_train_val():
    """固定 seed 拆分可复现 + 比例正确"""
    ids = list(range(100))
    train, val = split_train_val(ids, val_ratio=0.2, seed=42)
    assert len(val) == 20
    assert len(train) == 80
    assert set(train).isdisjoint(set(val))
    # 固定 seed: 两次拆分结果应一致
    train2, val2 = split_train_val(ids, val_ratio=0.2, seed=42)
    assert train == train2
    assert val == val2
    # 边界: 1 张图
    train, val = split_train_val([1], val_ratio=0.2, seed=1)
    assert len(val) + len(train) == 1


# ============== 2. annotations_to_yolo_lines ==============

def test_yolo_dataset_annotations_to_lines():
    """BBoxAnnotation 列表 → YOLO txt 行, 正确格式 + 过滤逻辑"""
    # 构造 mock BBoxAnnotation 对象 (用 SimpleNamespace 即可, 函数只读字段)
    from types import SimpleNamespace
    anns = [
        SimpleNamespace(
            category_id=10, x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.6,
        ),
        SimpleNamespace(
            category_id=20, x_min=0.5, y_min=0.5, x_max=0.9, y_max=0.9,
        ),
        SimpleNamespace(
            category_id=None, x_min=0.0, y_min=0.0, x_max=0.1, y_max=0.1,
        ),  # 无类别, 跳过
    ]
    class_idx = {10: 0, 20: 1}
    lines = annotations_to_yolo_lines(anns, class_idx)
    assert len(lines) == 2
    # 第一行: class=0, cx=0.3, cy=0.4, w=0.4, h=0.4
    parts0 = lines[0].split()
    assert parts0[0] == "0"
    assert abs(float(parts0[1]) - 0.3) < 1e-6
    assert abs(float(parts0[2]) - 0.4) < 1e-6
    # 第二行: class=1
    assert lines[1].split()[0] == "1"


# ============== 3. export_yolo_dataset 写 data.yaml ==============

@pytest.mark.asyncio
async def test_yolo_dataset_export_writes_yaml(
    client, auth_headers, temp_upload_dir, db_session
):
    """export_yolo_dataset 写出 images/{train,val} + labels/{train,val} + data.yaml"""
    ds_id, cat_id, img_ids = await _make_detection_ds_with_2_images(
        client, auth_headers
    )
    # 给 2 张图各写 1 个 bbox
    for img_id in img_ids:
        r = await client.post(
            "/api/detection/annotations/save",
            params={"image_id": img_id},
            headers=auth_headers,
            json={
                "x_min": 0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.5,
                "category_id": cat_id,
            },
        )
        assert r.status_code in (200, 201), r.text

    # 验证: db_session 直接查应能看见 2 条 bbox
    from sqlalchemy import select
    rows = (await db_session.execute(
        select(BBoxAnnotation).where(BBoxAnnotation.image_id.in_(img_ids))
    )).scalars().all()
    assert len(rows) == 2, f"db_session 看不到 bbox, rows={len(rows)}"
    db_session.expire_all()

    workdir = Path(temp_upload_dir) / "yolo_export"
    res = await export_yolo_dataset(
        db=db_session, dataset_id=ds_id, workdir=workdir,
        val_ratio=0.5, progress_cb=None,
    )
    assert res["workdir"] == str(workdir.resolve())
    assert res["classes"] == ["obj"]
    assert res["train_count"] + res["val_count"] == 2
    assert res["skipped_no_bbox"] == 0
    # data.yaml 内容
    yaml_text = (workdir / "data.yaml").read_text(encoding="utf-8")
    assert "nc: 1" in yaml_text
    assert "names: ['obj']" in yaml_text
    assert "train: images/train" in yaml_text
    # 至少一个 train + val 文件
    train_files = list((workdir / "labels" / "train").glob("*.txt"))
    val_files = list((workdir / "labels" / "val").glob("*.txt"))
    assert len(train_files) + len(val_files) == 2
    # 训练 txt 内容
    txt = train_files[0].read_text(encoding="utf-8") if train_files else val_files[0].read_text()
    assert "0 " in txt


# ============== 4. export 跳过缺图/无 bbox ==============

@pytest.mark.asyncio
async def test_yolo_dataset_export_skips_missing(
    client, auth_headers, temp_upload_dir, db_session
):
    """图 1 有 bbox, 图 2 无 bbox → skip count == 1, train + val = 1"""
    ds_id, cat_id, img_ids = await _make_detection_ds_with_2_images(
        client, auth_headers
    )
    # 仅给第一张图写 bbox
    r = await client.post(
        "/api/detection/annotations/save",
        params={"image_id": img_ids[0]},
        headers=auth_headers,
        json={
            "x_min": 0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.5,
            "category_id": cat_id,
        },
    )
    assert r.status_code in (200, 201)

    workdir = Path(temp_upload_dir) / "yolo_export_skip"
    res = await export_yolo_dataset(
        db=db_session, dataset_id=ds_id, workdir=workdir,
        val_ratio=0.5,
    )
    assert res["skipped_no_bbox"] == 1
    assert res["train_count"] + res["val_count"] == 1


# ============== 5. POST /train 启动 ==============

@pytest.mark.asyncio
async def test_train_endpoint_creates_task(
    client, auth_headers, temp_upload_dir
):
    """POST /train 触发 Celery .delay, 返回 task_id"""
    ds_id, _, _ = await _make_detection_ds_with_2_images(
        client, auth_headers
    )
    mock_result = MagicMock()
    mock_result.id = "fake-celery-task-id-1234"
    with patch(
        "app.workers.detection_tasks.train_detection_task.delay",
        return_value=mock_result,
    ) as m:
        resp = await client.post(
            "/api/detection/train", headers=auth_headers,
            json={
                "dataset_id": ds_id,
                "base_model": "yolov8n",
                "epochs": 5,
                "imgsz": 320,
                "batch_size": 4,
            },
        )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["celery_task_id"] == "fake-celery-task-id-1234"
    assert body["state"] == "PENDING"
    m.assert_called_once()
    kwargs = m.call_args.kwargs
    assert kwargs["dataset_id"] == ds_id
    assert kwargs["model_name"] == "yolov8n"
    assert kwargs["epochs"] == 5


# ============== 6. /train 任务类型守卫 ==============

@pytest.mark.asyncio
async def test_train_endpoint_wrong_task_type(
    client, auth_headers, temp_upload_dir
):
    """classification 数据集 → 400, 不投递任务"""
    resp = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "cls", "task_type": "classification"},
    )
    cls_ds_id = resp.json()["id"]

    with patch(
        "app.workers.detection_tasks.train_detection_task.delay"
    ) as m:
        r = await client.post(
            "/api/detection/train", headers=auth_headers,
            json={"dataset_id": cls_ds_id, "base_model": "yolov8n", "epochs": 3},
        )
    assert r.status_code == 400, r.text
    assert "detection" in r.text
    m.assert_not_called()


# ============== 7. /train Redis 不可达 ==============

@pytest.mark.asyncio
async def test_train_endpoint_redis_down_503(
    client, auth_headers, temp_upload_dir
):
    """Redis ping 抛异常 → 503, 不投递任务"""
    ds_id, _, _ = await _make_detection_ds_with_2_images(
        client, auth_headers
    )
    with patch(
        "app.api.detection.redis_client.ping",
        side_effect=ConnectionError("redis down"),
    ), patch(
        "app.workers.detection_tasks.train_detection_task.delay"
    ) as m:
        r = await client.post(
            "/api/detection/train", headers=auth_headers,
            json={"dataset_id": ds_id, "base_model": "yolov8n", "epochs": 1},
        )
    assert r.status_code == 503, r.text
    m.assert_not_called()


# ============== 8. /auto-annotate 校验 ==============

@pytest.mark.asyncio
async def test_auto_annotate_endpoint_validation(
    client, auth_headers, temp_upload_dir
):
    """非 detection 数据集 / 无效 ModelVersion → 400/404"""
    # 1) classification 数据集
    resp = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "cls_aa", "task_type": "classification"},
    )
    cls_ds = resp.json()["id"]
    mock_result = MagicMock(); mock_result.id = "x"
    with patch(
        "app.workers.detection_tasks.auto_annotate_detection_task.delay",
        return_value=mock_result,
    ) as m:
        r = await client.post(
            "/api/detection/auto-annotate",
            params={"dataset_id": cls_ds, "model_version_id": 1},
            headers=auth_headers,
        )
    assert r.status_code == 400
    m.assert_not_called()

    # 2) 不存在的 ModelVersion
    ds_id, _, _ = await _make_detection_ds_with_2_images(
        client, auth_headers, name="ds_aa"
    )
    with patch(
        "app.workers.detection_tasks.auto_annotate_detection_task.delay"
    ) as m:
        r = await client.post(
            "/api/detection/auto-annotate",
            params={"dataset_id": ds_id, "model_version_id": 99999},
            headers=auth_headers,
        )
    assert r.status_code == 404, r.text
    m.assert_not_called()


# ============== 9. /jobs/{id}/progress 返回 ==============

@pytest.mark.asyncio
async def test_job_progress_endpoint_returns_state(
    client, auth_headers, db_session
):
    """GET /jobs/{id}/progress 返回 ORM 字段 (含 task_type='detection')"""
    # 直接 DB 插一行, 避免依赖 Celery
    from app.models.user import User
    from app.models.dataset import Dataset
    user = (await db_session.execute(select(User).limit(1))).scalars().first()
    if user is None:
        pytest.skip("no user row")
    ds = Dataset(
        name="t_ds", task_type="detection",
        owner_id=user.id, image_count=0, annotated_count=0,
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)

    job = TrainingJob(
        celery_task_id="celery-abc", user_id=user.id, dataset_id=ds.id,
        base_model="yolov8n", model_name="test", task_type="detection",
        epochs=10, batch_size=4, learning_rate=0.0,
        state="PROGRESS", progress=42.0, message="running",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    r = await client.get(
        f"/api/detection/jobs/{job.id}/progress",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_type"] == "detection"
    assert body["state"] == "PROGRESS"
    assert body["progress"] == 42.0
    assert body["celery_task_id"] == "celery-abc"


# ============== 10. /jobs/{id}/progress 404 ==============

@pytest.mark.asyncio
async def test_job_progress_endpoint_404(client, auth_headers):
    """不存在的 job_id → 404"""
    r = await client.get(
        "/api/detection/jobs/9999999/progress",
        headers=auth_headers,
    )
    assert r.status_code == 404
