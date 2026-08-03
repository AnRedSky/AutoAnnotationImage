"""
test_evaluation_and_auto_label.py — 测评 & AI 预标注 综合功能测试
=================================================================

测试范围:
1. AI 预标注接口 POST /api/images/auto-label/{dataset_id}
   - 核心修复验证: NameError: name 'Dataset' is not defined (line 68)
   - 权限校验: 写权限校验
   - 无数据集场景
   - 无 fine-tune 模型回退到 timm 预训练
   - 有 fine-tune 模型
   - 各种业务参数 (threshold / model_id / use_finetune)

2. 置信度测评接口 POST /api/images/preview-confidence
   - 基础调用 (有图片 ids)
   - 空 image_ids 边界
   - 不存在图片 ids 边界
   - 权限校验
   - 不修改数据库 (核心 non-destructive 保证)
   - 阈值边界

3. 跨接口端到端: 测评 → 预标注

测试设计原则:
- 使用内存 SQLite 避免依赖真实数据库
- 真实调用路由 + 数据库, 模拟端到端流程
- 对 AI 推理部分 (ai_service) 做 mock, 避免依赖 GPU / 模型下载
"""
import io
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from PIL import Image
from sqlalchemy import select


# ============================================================
# 工具函数
# ============================================================
def _make_png_bytes(color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", (64, 64), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_prediction(top1: str, top1_conf: float, all_categories=None) -> dict:
    """构造一个 batch_predict 返回的预测, 含 top5 字段
    注: filter_predictions_to_categories 会读 top5
    """
    all_categories = all_categories or [top1]
    top5 = [{"label": c, "confidence": top1_conf if c == top1 else 0.5} for c in all_categories]
    return {
        "top1": top1,
        "top1_conf": top1_conf,
        "top5": top5,
        "candidates": top5,
    }


async def _make_dataset_with_images(
    client, auth_headers, count=3, with_categories=True, task_type="classification",
    name_prefix="ds_eval",
):
    """辅助: 创建数据集 + 类别 + N 张图 (count=0 时只创建空数据集)"""
    payload = {"name": f"{name_prefix}_{count}", "task_type": task_type}
    if with_categories:
        payload["category_names"] = ["cat_a", "cat_b", "cat_c"]
    r = await client.post("/api/datasets", headers=auth_headers, json=payload)
    assert r.status_code in (200, 201), r.text
    ds_id = r.json()["id"]
    if count > 0:
        files = [
            ("files", (f"img_{i}.png", _make_png_bytes((i * 30, i * 30, i * 30)), "image/png"))
            for i in range(count)
        ]
        r2 = await client.post(f"/api/images/upload/{ds_id}", headers=auth_headers, files=files)
        assert r2.status_code in (200, 201), r2.text
    return ds_id


async def _create_fake_model_version(
    db_session,
    *,
    dataset_id: int,
    name: str = "fake_mv",
    is_active: bool = True,
    num_classes: int = 3,
    file_path: str = "/tmp/fake_model.pth",
    class_names=None,
):
    """直接往 DB 插一个 ModelVersion (绕开真实训练, 用于测试 fine-tune 分支)"""
    from app.tasks.model.model_version import ModelVersion
    mv = ModelVersion(
        name=name,
        base_model="efficientnet_b0",
        dataset_id=dataset_id,
        task_type="classification",
        num_classes=num_classes,
        class_names=class_names or ["cat_a", "cat_b", "cat_c"],
        file_path=file_path,
        is_active=is_active,
        accuracy=0.95,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)
    return mv


def _mock_svc_with_predictions(predictions, *, current_model_name="efficientnet_b0",
                                current_model_path=None):
    """构造一个 ai_service MagicMock, 包含 batch_predict 返回值 + 必要属性"""
    mock = MagicMock()
    mock.batch_predict = AsyncMock(return_value=predictions)
    mock.load_local = AsyncMock()
    mock.load_pretrained = AsyncMock()
    mock.current_model_name = current_model_name
    mock.current_model_path = current_model_path
    mock.set_label_map = MagicMock()
    return mock


# ============================================================
# AI 预标注: 核心修复验证
# ============================================================
@pytest.mark.asyncio
async def test_auto_label_no_name_error(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-AUTOLABEL-NO-NAME-ERROR: 验证 v3.3.0 P0 修复

    之前 auto_label 函数内部 db.get(Dataset, dataset_id) 时
    Dataset 未导入, 触发 NameError. 现已在函数内导入, 此测试
    关键路径必须走通 (即 NameError 不再抛出).
    """
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    pred = _make_prediction("cat_a", 0.9, all_categories=["cat_a", "cat_b", "cat_c"])
    mock_svc = _mock_svc_with_predictions([pred, pred])

    with patch("app.tasks.api.image.auto_label.ai_service", mock_svc):
        r = await client.post(
            f"/api/images/auto-label/{ds_id}",
            headers=auth_headers,
            params={"confidence_threshold": 0.5, "use_finetune": False},
        )

    # 关键断言: 状态码不是 500, 不再是 NameError
    assert r.status_code == 200, f"NameError 仍存在: {r.text}"
    body = r.json()
    assert "total" in body
    assert "auto_labeled" in body
    # 项目无 fine-tune + use_finetune=False → 走 timm 预训练 (但不属于"回退"
    # 语义, fallback 仅在 use_finetune=True 时为 True)
    assert body["used_finetune"] is False


@pytest.mark.asyncio
async def test_auto_label_404_on_missing_dataset(
    client, auth_headers, temp_upload_dir,
):
    """TC-AUTOLABEL-404: 数据集不存在返回 404, 不抛 NameError"""
    mock_svc = _mock_svc_with_predictions([])
    with patch("app.tasks.api.image.auto_label.ai_service", mock_svc):
        r = await client.post(
            "/api/images/auto-label/99999",
            headers=auth_headers,
            params={"confidence_threshold": 0.5, "use_finetune": False},
        )
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_auto_label_uses_finetune(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-AUTOLABEL-FINETUNE: 有 fine-tune 模型时走 fine-tune 分支"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    fake_model_path = temp_upload_dir / "fake_model.pth"
    fake_model_path.write_bytes(b"fake weights")
    mv = await _create_fake_model_version(
        db_session,
        dataset_id=ds_id,
        file_path=str(fake_model_path),
        is_active=True,
        class_names=["cat_a", "cat_b", "cat_c"],
    )

    pred = _make_prediction("cat_a", 0.95, all_categories=["cat_a", "cat_b", "cat_c"])
    mock_svc = _mock_svc_with_predictions([pred, pred],
                                          current_model_path=str(fake_model_path))

    with patch("app.tasks.api.image.auto_label.ai_service", mock_svc):
        r = await client.post(
            f"/api/images/auto-label/{ds_id}",
            headers=auth_headers,
            params={"confidence_threshold": 0.6, "use_finetune": True},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_finetune"] is True
    assert body["model_id"] == mv.id
    assert body["finetune_name"] == "fake_mv"


@pytest.mark.asyncio
async def test_auto_label_no_pending(
    client, auth_headers, temp_upload_dir,
):
    """TC-AUTOLABEL-EMPTY: 数据集无 pending 图片时, 不调用推理, 直接返回空结果"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=0)
    mock_svc = _mock_svc_with_predictions([])

    with patch("app.tasks.api.image.auto_label.ai_service", mock_svc):
        r = await client.post(
            f"/api/images/auto-label/{ds_id}",
            headers=auth_headers,
            params={"confidence_threshold": 0.5, "use_finetune": False},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["auto_labeled"] == 0
    # batch_predict 不应被调用 (因为没图片)
    mock_svc.batch_predict.assert_not_called()
    assert "No pending images" in body["message"]


@pytest.mark.asyncio
async def test_auto_label_threshold_filters_low_confidence(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-AUTOLABEL-THRESHOLD: 阈值边界 - 低于阈值的不被标注"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    img_list = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_ids = [x["id"] for x in img_list.json()["items"]]

    preds = [
        _make_prediction("cat_a", 0.71, all_categories=["cat_a", "cat_b", "cat_c"]),
        _make_prediction("cat_b", 0.69, all_categories=["cat_a", "cat_b", "cat_c"]),
    ]
    mock_svc = _mock_svc_with_predictions(preds)

    with patch("app.tasks.api.image.auto_label.ai_service", mock_svc):
        r = await client.post(
            f"/api/images/auto-label/{ds_id}",
            headers=auth_headers,
            params={"confidence_threshold": 0.70, "use_finetune": False},
        )
    assert r.status_code == 200
    body = r.json()
    # 0.71 >= 0.70 标注; 0.69 < 0.70 不标 (need_human)
    assert body["auto_labeled"] == 1
    assert body["need_human"] == 1


@pytest.mark.asyncio
async def test_auto_label_writes_audit_log(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-AUTOLABEL-AUDIT: 成功标注后, AnnotationLog 应有记录"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=1)
    img_list = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_id = img_list.json()["items"][0]["id"]

    pred = _make_prediction("cat_a", 0.99, all_categories=["cat_a", "cat_b", "cat_c"])
    mock_svc = _mock_svc_with_predictions([pred])

    with patch("app.tasks.api.image.auto_label.ai_service", mock_svc):
        r = await client.post(
            f"/api/images/auto-label/{ds_id}",
            headers=auth_headers,
            params={"confidence_threshold": 0.5, "use_finetune": False},
        )
    assert r.status_code == 200, r.text
    assert r.json()["auto_labeled"] == 1

    # 验证 AnnotationLog
    from app.tasks.model.annotation_log import AnnotationLog
    logs = (await db_session.execute(
        select(AnnotationLog).where(AnnotationLog.image_id == img_id)
    )).scalars().all()
    assert len(logs) == 1
    assert logs[0].action == "ai_predict"


# ============================================================
# 测评接口: 端到端
# ============================================================
@pytest.mark.asyncio
async def test_preview_confidence_basic(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-PREVIEW-BASIC: 基础测评 - 3 张图 → 返回 top1 + would_label / need_human / no_match

    关键: 不创建 fine-tune, 让 use_finetune=True 自动回退到 timm 预训练,
    这样 filter_predictions_to_categories 会被调用, 触发 no_match 路径
    """
    ds_id = await _make_dataset_with_images(client, auth_headers, count=3)
    img_list = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_ids = [x["id"] for x in img_list.json()["items"]]

    # 注意: 不创建 ModelVersion, 让 use_finetune=True 自动回退到 timm
    # 这样 used_finetune=False, filter_predictions_to_categories 会被调用

    # 构造 3 种典型预测:
    # 1) 在类目内 + 高于阈值 → would_label
    # 2) 在类目内 + 低于阈值 → need_human (reason=below_threshold)
    # 3) 不在类目 (top5 都是 unknown) → filter 后变 None → no_match
    preds = [
        _make_prediction("cat_a", 0.95, all_categories=["cat_a", "cat_b", "cat_c"]),
        _make_prediction("cat_b", 0.55, all_categories=["cat_a", "cat_b", "cat_c"]),
        _make_prediction("unknown_xyz", 0.99, all_categories=["unknown_xyz"]),
    ]
    mock_svc = _mock_svc_with_predictions(preds)

    with patch("app.tasks.api.preview.classification.ai_service", mock_svc):
        r = await client.post(
            "/api/images/preview-confidence",
            headers=auth_headers,
            json={
                "dataset_id": ds_id,
                "image_ids": img_ids,
                "model_id": None,
                "confidence_threshold": 0.6,
                "use_finetune": True,
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["would_label"] == 1  # 第一张
    assert body["need_human"] == 1   # 第二张 (低于阈值)
    assert body["no_match"] == 1     # 第三张 (不在类目, 被 filter 变 None)
    assert body["used_finetune"] is False  # 回退到 timm
    assert body["fallback_to_pretrained"] is True  # use_finetune=True 但 used_finetune=False
    # 验证 items 的 reason 分布
    items = body["items"]
    reasons = [it["reason"] for it in items]
    assert "would_label" in reasons
    assert "below_threshold" in reasons
    assert "no_match" in reasons


@pytest.mark.asyncio
async def test_preview_confidence_does_not_modify_db(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-PREVIEW-NON-DESTRUCTIVE: 测评接口**不能**修改任何图片状态

    这是测评的核心契约: dry-run, 弹窗显示用, 选定后再点预标注才写库.
    """
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    img_list = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    items_before = img_list.json()["items"]
    img_ids = [x["id"] for x in items_before]

    fake_model_path = temp_upload_dir / "fake_model.pth"
    fake_model_path.write_bytes(b"fake")
    await _create_fake_model_version(
        db_session, dataset_id=ds_id,
        file_path=str(fake_model_path), is_active=True,
    )

    pred_a = _make_prediction("cat_a", 0.99, all_categories=["cat_a", "cat_b", "cat_c"])
    pred_b = _make_prediction("cat_b", 0.99, all_categories=["cat_a", "cat_b", "cat_c"])
    mock_svc = _mock_svc_with_predictions([pred_a, pred_b])

    with patch("app.tasks.api.preview.classification.ai_service", mock_svc):
        r = await client.post(
            "/api/images/preview-confidence",
            headers=auth_headers,
            json={
                "dataset_id": ds_id,
                "image_ids": img_ids,
                "confidence_threshold": 0.5,
                "use_finetune": True,
            },
        )
    assert r.status_code == 200

    # 验证: 图片 status / ai_prediction 都没被改
    img_list_after = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    items_after = img_list_after.json()["items"]
    for before, after in zip(items_before, items_after):
        assert before["status"] == after["status"] == "pending"
        assert before.get("ai_prediction") == after.get("ai_prediction")


@pytest.mark.asyncio
async def test_preview_confidence_empty_image_ids(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-PREVIEW-EMPTY-IDS: 空 image_ids 仍要走权限校验 (v3.3.0 P0)"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=0)
    r = await client.post(
        "/api/images/preview-confidence",
        headers=auth_headers,
        json={
            "dataset_id": ds_id,
            "image_ids": [],
            "confidence_threshold": 0.6,
            "use_finetune": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["would_label"] == 0


@pytest.mark.asyncio
async def test_preview_confidence_dataset_not_found(
    client, auth_headers, temp_upload_dir,
):
    """TC-PREVIEW-404: 不存在 dataset 返回 404"""
    r = await client.post(
        "/api/images/preview-confidence",
        headers=auth_headers,
        json={
            "dataset_id": 99999,
            "image_ids": [1, 2, 3],
            "confidence_threshold": 0.6,
            "use_finetune": True,
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_preview_confidence_permission_denied(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-PREVIEW-403: 其他用户的 dataset, 测评应被拒"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=1)

    # 另起一个用户 B
    from app.admin.model.user import User
    from app.middleware.security.security import hash_password
    user_b = User(
        username="userB", email="b@example.com",
        password_hash=hash_password("passwordB"),
        role="annotator", is_active=True,
    )
    db_session.add(user_b)
    await db_session.commit()

    resp = await client.post(
        "/api/auth/login", data={"username": "userB", "password": "passwordB"},
    )
    assert resp.status_code == 200
    token_b = resp.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # B 试图测评 A 的 dataset
    r = await client.post(
        "/api/images/preview-confidence",
        headers=headers_b,
        json={
            "dataset_id": ds_id,
            "image_ids": [1, 2, 3],
            "confidence_threshold": 0.6,
            "use_finetune": True,
        },
    )
    assert r.status_code == 403, f"应被拒绝, 实际 {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_preview_confidence_threshold_logic(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-PREVIEW-THRESHOLD: 阈值边界 - 0.7 阈值下 0.69 应被拒, 0.71 应被收"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    img_list = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_ids = [x["id"] for x in img_list.json()["items"]]

    fake_model_path = temp_upload_dir / "fake_model.pth"
    fake_model_path.write_bytes(b"fake")
    await _create_fake_model_version(
        db_session, dataset_id=ds_id,
        file_path=str(fake_model_path), is_active=True,
    )

    preds = [
        _make_prediction("cat_a", 0.71, all_categories=["cat_a", "cat_b", "cat_c"]),
        _make_prediction("cat_a", 0.69, all_categories=["cat_a", "cat_b", "cat_c"]),
    ]
    mock_svc = _mock_svc_with_predictions(preds)

    with patch("app.tasks.api.preview.classification.ai_service", mock_svc):
        r = await client.post(
            "/api/images/preview-confidence",
            headers=auth_headers,
            json={
                "dataset_id": ds_id,
                "image_ids": img_ids,
                "confidence_threshold": 0.70,
                "use_finetune": True,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["would_label"] == 1   # 0.71 满足
    assert body["need_human"] == 1    # 0.69 不满足


# ============================================================
# 跨接口: 测评 → 预标注 (典型用户路径)
# ============================================================
@pytest.mark.asyncio
async def test_full_workflow_preview_then_autolabel(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-WORKFLOW-PREVIEW-THEN-LABEL: 完整用户路径
    1) 测评 (不写库)  → 看到 would_label=1
    2) 预标注 (写库)  → auto_labeled=1
    3) 验证: 标注后 status 变 ai_labeled
    """
    ds_id = await _make_dataset_with_images(client, auth_headers, count=1)
    img_list = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_id = img_list.json()["items"][0]["id"]

    fake_model_path = temp_upload_dir / "fake_model.pth"
    fake_model_path.write_bytes(b"fake")
    await _create_fake_model_version(
        db_session, dataset_id=ds_id,
        file_path=str(fake_model_path), is_active=True,
    )

    pred = _make_prediction("cat_a", 0.9, all_categories=["cat_a", "cat_b", "cat_c"])

    # 第一步: 测评
    mock_preview = _mock_svc_with_predictions([pred])
    with patch("app.tasks.api.preview.classification.ai_service", mock_preview):
        r1 = await client.post(
            "/api/images/preview-confidence",
            headers=auth_headers,
            json={
                "dataset_id": ds_id,
                "image_ids": [img_id],
                "confidence_threshold": 0.6,
                "use_finetune": True,
            },
        )
    assert r1.status_code == 200
    assert r1.json()["would_label"] == 1

    # 验证: 测评后, 图片仍 pending
    img_after_preview = (await client.get(
        f"/api/images/list/{ds_id}", headers=auth_headers
    )).json()["items"][0]
    assert img_after_preview["status"] == "pending"

    # 第二步: 真实预标注
    mock_label = _mock_svc_with_predictions([pred],
                                            current_model_path=str(fake_model_path))
    with patch("app.tasks.api.image.auto_label.ai_service", mock_label):
        r2 = await client.post(
            f"/api/images/auto-label/{ds_id}",
            headers=auth_headers,
            params={"confidence_threshold": 0.6, "use_finetune": True},
        )
    assert r2.status_code == 200
    assert r2.json()["auto_labeled"] == 1

    # 验证: 标注后, status=ai_labeled
    img_after_label = (await client.get(
        f"/api/images/list/{ds_id}", headers=auth_headers
    )).json()["items"][0]
    assert img_after_label["status"] == "ai_labeled"


@pytest.mark.asyncio
async def test_auto_label_permission_denied_cross_user(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-AUTOLABEL-403: 跨用户写权限拦截 (require_write=True)"""
    ds_id = await _make_dataset_with_images(client, auth_headers, count=1)

    from app.admin.model.user import User
    from app.middleware.security.security import hash_password
    user_b = User(
        username="userB_label", email="b_label@example.com",
        password_hash=hash_password("passwordB"),
        role="annotator", is_active=True,
    )
    db_session.add(user_b)
    await db_session.commit()

    resp = await client.post(
        "/api/auth/login", data={"username": "userB_label", "password": "passwordB"},
    )
    token_b = resp.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    pred = _make_prediction("cat_a", 0.9, all_categories=["cat_a", "cat_b", "cat_c"])
    mock_svc = _mock_svc_with_predictions([pred])

    with patch("app.tasks.api.image.auto_label.ai_service", mock_svc):
        r = await client.post(
            f"/api/images/auto-label/{ds_id}",
            headers=headers_b,
            params={"confidence_threshold": 0.5, "use_finetune": False},
        )
    assert r.status_code == 403


# ============================================================
# v3.4.1: 测评结果 no_match / infer_failed 区分 + 跨数据集 model_id 校验
# ============================================================
@pytest.mark.asyncio
async def test_preview_infer_failed_when_batch_predict_returns_none(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-PREVIEW-INFER-FAILED: batch_predict 返回 None 时算 infer_failed (非 no_match)

    之前 pred is None 一律归为 no_match, 误导用户 (以为图片与类目不匹配).
    修复后: used_finetune=True 时 pred=None 必为推理失败, reason=infer_failed,
    item 附 error 字段, 顶层 infer_failed 计数.
    """
    from unittest.mock import MagicMock, patch
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    img_list = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_ids = [x["id"] for x in img_list.json()["items"]]

    # 造一个 fine-tune 模型, 让 preview 走 used_finetune=True
    fake_model_path = temp_upload_dir / "fake_model_infer.pth"
    fake_model_path.write_bytes(b"fake")
    await _create_fake_model_version(
        db_session, dataset_id=ds_id,
        file_path=str(fake_model_path), is_active=True,
    )

    # 让 batch_predict 返回 [None, None] — 模拟读图/forward 失败
    failing_svc = MagicMock()
    failing_svc.batch_predict = AsyncMock(return_value=[None, None])
    failing_svc.load_local = AsyncMock()
    failing_svc.load_pretrained = AsyncMock()
    failing_svc.current_model_name = "mobilenetv3_large_100"
    failing_svc.current_model_path = str(fake_model_path)
    failing_svc.set_label_map = MagicMock()
    failing_svc._custom_label_map = {0: "cat_a", 1: "cat_b", 2: "cat_c"}

    with patch("app.tasks.api.preview.classification.ai_service", failing_svc):
        r = await client.post(
            "/api/images/preview-confidence",
            headers=auth_headers,
            json={
                "dataset_id": ds_id,
                "image_ids": img_ids,
                "confidence_threshold": 0.6,
                "use_finetune": True,
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # 关键断言: used_finetune=True + pred=None → 走 infer_failed, 不再误归 no_match
    assert body["infer_failed"] == 2
    assert body["no_match"] == 0
    assert body["would_label"] == 0
    for it in body["items"]:
        assert it["reason"] == "infer_failed"
        assert it["error"]   # error 字段必填


@pytest.mark.asyncio
async def test_preview_cross_dataset_model_id_rejected(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-PREVIEW-CROSS-DATASET: model_id 来自别数据集时返回 400

    修复前: 跨数据集的 ModelVersion 也能加载, 用当前数据集的 _custom_label_map
    映射别数据集训练的 idx, 推理输出几乎全 no_match, 用户无法定位.
    修复后: _resolve_finetune_model 显式校验 mv.dataset_id == current dataset_id.
    """
    # 数据集 A (有 fine-tune)
    ds_a = await _make_dataset_with_images(client, auth_headers, count=1, name_prefix="dsA")
    fake_a = temp_upload_dir / "model_a.pth"
    fake_a.write_bytes(b"fake")
    mv_a = await _create_fake_model_version(
        db_session, dataset_id=ds_a,
        file_path=str(fake_a), is_active=True,
    )
    # 数据集 B (无 fine-tune, 但测评时误传 mv_a.id)
    ds_b = await _make_dataset_with_images(client, auth_headers, count=1, name_prefix="dsB")
    img_list = await client.get(f"/api/images/list/{ds_b}", headers=auth_headers)
    img_ids = [x["id"] for x in img_list.json()["items"]]

    r = await client.post(
        "/api/images/preview-confidence",
        headers=auth_headers,
        json={
            "dataset_id": ds_b,
            "image_ids": img_ids,
            "model_id": mv_a.id,  # 跨数据集的 mv
            "confidence_threshold": 0.6,
            "use_finetune": True,
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json().get("detail", "")
    assert "不属于当前数据集" in detail


@pytest.mark.asyncio
async def test_preview_warning_when_top1_all_class_xxx(
    client, auth_headers, db_session, temp_upload_dir,
):
    """TC-PREVIEW-WARNING-CLASS-XXX: fine-tune top1 全是 class_X 兜底时给 warning

    当 _custom_label_map 没覆盖到模型实际输出的 idx, top1 会退化为 class_X 兜底.
    这意味着模型与数据集不匹配 (训练 num_classes / 类目顺序与当前不同).
    """
    from unittest.mock import MagicMock, patch
    ds_id = await _make_dataset_with_images(client, auth_headers, count=2)
    img_list = await client.get(f"/api/images/list/{ds_id}", headers=auth_headers)
    img_ids = [x["id"] for x in img_list.json()["items"]]

    fake_model_path = temp_upload_dir / "fake_model_warn.pth"
    fake_model_path.write_bytes(b"fake")
    await _create_fake_model_version(
        db_session, dataset_id=ds_id,
        file_path=str(fake_model_path), is_active=True,
    )

    # 预测 top1 全是 class_X (模拟 model 与 dataset 不匹配)
    preds = [
        {"top1": "class_532", "top1_conf": 0.9,
         "top5": [{"label": "class_532", "confidence": 0.9}]},
        {"top1": "class_580", "top1_conf": 0.8,
         "top5": [{"label": "class_580", "confidence": 0.8}]},
    ]
    warn_svc = MagicMock()
    warn_svc.batch_predict = AsyncMock(return_value=preds)
    warn_svc.load_local = AsyncMock()
    warn_svc.load_pretrained = AsyncMock()
    warn_svc.current_model_name = "mobilenetv3_large_100"
    warn_svc.current_model_path = str(fake_model_path)
    warn_svc.set_label_map = MagicMock()
    warn_svc._custom_label_map = {0: "cat_a", 1: "cat_b", 2: "cat_c"}  # 故意不覆盖 532/580

    with patch("app.tasks.api.preview.classification.ai_service", warn_svc):
        r = await client.post(
            "/api/images/preview-confidence",
            headers=auth_headers,
            json={
                "dataset_id": ds_id,
                "image_ids": img_ids,
                "confidence_threshold": 0.6,
                "use_finetune": True,
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["warning"]
    assert "模型与当前数据集不匹配" in body["warning"]

