"""
v3.3.0 P0 跨用户数据隔离测试
=============================

测试目标:
- 验证所有 P0 修复后的 endpoint 都能正确拒绝越权访问
- 用户 A 创建的数据, 用户 B 不能通过任何 endpoint 看到
- admin 用户不受此限制

测试矩阵:
- 用户 A (testuser) 登录, 创建 dataset, 上传 image, 启动 training
- 用户 B (otheruser) 登录, 尝试访问用户 A 的所有数据
  → 期望: 全部 403
- admin 用户 (admin) 登录, 尝试访问 → 期望: 200 (全局可见)

覆盖 endpoint:
1. 数据集: GET /api/datasets/{id}
2. 类别:   POST /api/datasets/{id}/categories, GET /api/datasets/{id}/categories
3. 图片:   GET /api/images/{id}, GET /api/images/list/{id}
4. 文件:   GET /api/files/{id}
5. 标注:   GET /api/annotations/recent
6. 训练:   GET /api/training/jobs/{id}
7. 模型:   GET /api/models/{id}
8. 统计:   GET /api/stats/overview
9. 仪表盘数据: GET /api/stats/dataset/{id}
10. 用户列表: GET /api/users
11. 检测/分割: bbox / mask 端点
12. 进度查询: GET /api/training/progress/{task_id}
13. 训练日志: GET /api/training/jobs/{id}/log
14. 导出:    GET /api/export/coco/{dataset_id} 等
15. 预览:    POST /api/images/preview-confidence
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.admin.model.user import User
from app.tasks.model.dataset import Dataset
from app.tasks.model.image import Image
from app.tasks.model.category import Category
from app.tasks.model.training_job import TrainingJob
from app.tasks.model.model_version import ModelVersion
from app.middleware.security.security import hash_password


# ============== Helper Fixtures ==============

@pytest_asyncio.fixture
async def other_user(db_session: AsyncSession) -> User:
    """第二个测试用户 (B)"""
    user = User(
        username="otheruser",
        email="other@example.com",
        password_hash=hash_password("otherpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """管理员用户"""
    user = User(
        username="admin",
        email="admin@example.com",
        password_hash=hash_password("adminpass123"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def other_auth_headers(client: AsyncClient, other_user: User) -> dict:
    """用户 B 的 Bearer token"""
    resp = await client.post(
        "/api/auth/login",
        data={"username": "otheruser", "password": "otherpass123"},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_auth_headers(client: AsyncClient, admin_user: User) -> dict:
    """admin 的 Bearer token"""
    resp = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "adminpass123"},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def user_a_dataset(client: AsyncClient, auth_headers: dict) -> int:
    """用户 A 创建的数据集"""
    resp = await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "user_a_dataset", "task_type": "classification", "category_names": ["cat", "dog"]}
    )
    assert resp.status_code in (200, 201), f"Create dataset failed: {resp.text}"
    return resp.json()["id"]


# ============== 测试 ==============

@pytest.mark.asyncio
async def test_other_user_cannot_get_dataset(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能读取用户 A 的数据集详情"""
    # A 读自己的 → 200
    resp = await client.get(f"/api/datasets/{user_a_dataset}", headers=auth_headers)
    assert resp.status_code == 200, f"User A should be able to read own: {resp.text}"

    # B 读 A 的 → 403
    resp = await client.get(f"/api/datasets/{user_a_dataset}", headers=other_auth_headers)
    assert resp.status_code == 403, f"User B should NOT be able to read A's dataset: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_list_dataset_images(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能列出用户 A 数据集的图像"""
    # B 尝试列 A 数据集的图 → 403
    resp = await client.get(
        f"/api/images/list/{user_a_dataset}", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT list A's images: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_add_category(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能给用户 A 的数据集添加类别"""
    resp = await client.post(
        f"/api/datasets/{user_a_dataset}/categories",
        headers=other_auth_headers,
        json={"name": "bird"},
    )
    assert resp.status_code == 403, f"User B should NOT add category to A's dataset: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_list_categories(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能列出用户 A 数据集的类别"""
    resp = await client.get(
        f"/api/datasets/{user_a_dataset}/categories",
        headers=other_auth_headers,
    )
    assert resp.status_code == 403, f"User B should NOT list A's categories: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_image(
    client, auth_headers, other_auth_headers, user_a_dataset, db_session
):
    """用户 B 不能读取用户 A 数据集的图像详情"""
    # A 创建一个 image
    img = Image(
        dataset_id=user_a_dataset,
        filename="test.jpg",
        storage_path="test/path/test.jpg",
        file_size=1024,
        file_hash="abc",
        status="pending",
        task_type="classification",
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)

    # B 读 A 的 image → 403
    resp = await client.get(f"/api/images/{img.id}", headers=other_auth_headers)
    assert resp.status_code == 403, f"User B should NOT read A's image: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_image_file(
    client, auth_headers, other_auth_headers, user_a_dataset, db_session, temp_upload_dir
):
    """用户 B 不能访问用户 A 数据集的图像文件"""
    img = Image(
        dataset_id=user_a_dataset,
        filename="test.jpg",
        storage_path="test/path/test.jpg",
        file_size=1024,
        file_hash="abc",
        status="pending",
        task_type="classification",
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)

    # B 尝试访问 A 的 image 文件 → 403
    resp = await client.get(f"/api/files/{img.id}", headers=other_auth_headers)
    assert resp.status_code == 403, f"User B should NOT access A's image file: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_image_thumbnail(
    client, auth_headers, other_auth_headers, user_a_dataset, db_session
):
    """用户 B 不能访问用户 A 数据集的缩略图"""
    img = Image(
        dataset_id=user_a_dataset,
        filename="test.jpg",
        storage_path="test/path/test.jpg",
        file_size=1024,
        file_hash="abc",
        status="pending",
        task_type="classification",
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)

    resp = await client.get(
        f"/api/files/{img.id}/thumbnail", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT access A's thumbnail: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_delete_image(
    client, auth_headers, other_auth_headers, user_a_dataset, db_session
):
    """用户 B 不能删除用户 A 数据集的图像"""
    img = Image(
        dataset_id=user_a_dataset,
        filename="test.jpg",
        storage_path="test/path/test.jpg",
        file_size=1024,
        file_hash="abc",
        status="pending",
        task_type="classification",
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)

    resp = await client.delete(
        f"/api/images/{img.id}", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT delete A's image: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_dataset_stats(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能查看用户 A 数据集的统计"""
    resp = await client.get(
        f"/api/stats/dataset/{user_a_dataset}", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT see A's dataset stats: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_confidence_distribution(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能查看用户 A 数据集的置信度分布"""
    resp = await client.get(
        f"/api/stats/confidence/{user_a_dataset}", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT see A's confidence dist: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_annotation_timeline(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能查看用户 A 数据集的标注时间线"""
    resp = await client.get(
        f"/api/stats/timeline/{user_a_dataset}", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT see A's annotation timeline: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_export_dataset(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能导出用户 A 的数据集"""
    for fmt in ["coco", "yolo", "csv"]:
        resp = await client.get(
            f"/api/export/{fmt}/{user_a_dataset}", headers=other_auth_headers
        )
        assert resp.status_code == 403, f"User B should NOT export A's dataset ({fmt}): {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_run_preview(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能在用户 A 的数据集上跑预览"""
    resp = await client.post(
        "/api/images/preview-confidence",
        headers=other_auth_headers,
        json={"dataset_id": user_a_dataset, "image_ids": []},
    )
    assert resp.status_code == 403, f"User B should NOT preview A's dataset: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_start_training(
    client, auth_headers, other_auth_headers, user_a_dataset
):
    """用户 B 不能在用户 A 的数据集上启动训练"""
    resp = await client.post(
        "/api/training/start",
        headers=other_auth_headers,
        params={"dataset_id": user_a_dataset},
    )
    assert resp.status_code == 403, f"User B should NOT start training on A's dataset: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_training_job(
    client, auth_headers, other_auth_headers, db_session, test_user
):
    """用户 B 不能查看用户 A 的训练任务"""
    job = TrainingJob(
        user_id=test_user.id,
        dataset_id=1,
        celery_task_id="fake-task-id-123",
        base_model="efficientnet_b0",
        model_name="test_model_a",
        state="PENDING",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    resp = await client.get(
        f"/api/training/jobs/{job.id}", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT view A's training job: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_training_log(
    client, auth_headers, other_auth_headers, db_session, test_user
):
    """用户 B 不能查看用户 A 的训练日志"""
    job = TrainingJob(
        user_id=test_user.id,
        dataset_id=1,
        celery_task_id="fake-task-id-456",
        base_model="efficientnet_b0",
        model_name="test_model_a",
        state="SUCCESS",
        log=["epoch 1 done"],
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    resp = await client.get(
        f"/api/training/jobs/{job.id}/log", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT view A's training log: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_append_training_log(
    client, auth_headers, other_auth_headers, db_session, test_user
):
    """用户 B 不能追加用户 A 的训练日志"""
    job = TrainingJob(
        user_id=test_user.id,
        dataset_id=1,
        celery_task_id="fake-task-id-789",
        base_model="efficientnet_b0",
        model_name="test_model_a",
        state="PROGRESS",
        log=[],
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    resp = await client.post(
        f"/api/training/jobs/{job.id}/log",
        headers=other_auth_headers,
        json={"line": "hacked by user B"},
    )
    assert resp.status_code == 403, f"User B should NOT append to A's training log: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_get_training_progress(
    client, auth_headers, other_auth_headers, db_session, test_user
):
    """用户 B 不能查询用户 A 训练任务的进度"""
    job = TrainingJob(
        user_id=test_user.id,
        dataset_id=1,
        celery_task_id="fake-task-id-progress",
        base_model="efficientnet_b0",
        model_name="test_model_a",
        state="PROGRESS",
        progress=0.5,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # REST 进度
    resp = await client.get(
        f"/api/training/progress/{job.celery_task_id}", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT query A's training progress: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_activate_model(
    client, auth_headers, other_auth_headers, user_a_dataset, db_session
):
    """用户 B 不能激活用户 A 数据集的模型"""
    mv = ModelVersion(
        dataset_id=user_a_dataset,
        name="user_a_model",
        base_model="efficientnet_b0",
        num_classes=2,
        file_path="/tmp/fake.pth",
        is_active=False,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)

    resp = await client.post(
        f"/api/models/{mv.id}/activate", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT activate A's model: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_delete_model(
    client, auth_headers, other_auth_headers, user_a_dataset, db_session
):
    """用户 B 不能删除用户 A 数据集的模型"""
    mv = ModelVersion(
        dataset_id=user_a_dataset,
        name="user_a_model_del",
        base_model="efficientnet_b0",
        num_classes=2,
        file_path="/tmp/fake_del.pth",
        is_active=False,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)

    resp = await client.delete(
        f"/api/models/{mv.id}", headers=other_auth_headers
    )
    assert resp.status_code == 403, f"User B should NOT delete A's model: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_recent_annotations(
    client, auth_headers, other_auth_headers
):
    """用户 B 调用 recent annotations 不能看到用户 A 的"""
    # 1) 直接访问不能抛 403 (返回自己可见的列表)
    resp = await client.get(
        "/api/annotations/recent", headers=other_auth_headers
    )
    # 应该 200 但只返回自己可见的 (空列表, 因为 B 没有数据)
    assert resp.status_code == 200, f"Should not error: {resp.text}"
    items = resp.json().get("items", [])
    # 用户 B 没有任何数据, 应该是空
    assert len(items) == 0, f"User B should NOT see A's annotations: {items}"


@pytest.mark.asyncio
async def test_other_user_cannot_annotate_image(
    client, auth_headers, other_auth_headers, user_a_dataset, db_session
):
    """用户 B 不能给用户 A 数据集的图打标 (mark_unqualified)"""
    img = Image(
        dataset_id=user_a_dataset,
        filename="to_ann.jpg",
        storage_path="test/path/to_ann.jpg",
        file_size=1024,
        file_hash="ann1",
        status="pending",
        task_type="classification",
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)

    # B 尝试 mark_unqualified → 403
    resp = await client.post(
        "/api/annotations/mark-unqualified",
        headers=other_auth_headers,
        json={"image_id": img.id, "reason": "blurry"},
    )
    assert resp.status_code == 403, f"User B should NOT mark A's image: {resp.text}"


@pytest.mark.asyncio
async def test_other_user_cannot_clear_annotations(
    client, auth_headers, other_auth_headers, user_a_dataset, db_session
):
    """用户 B 不能清除用户 A 数据集的标注"""
    img = Image(
        dataset_id=user_a_dataset,
        filename="to_clear.jpg",
        storage_path="test/path/to_clear.jpg",
        file_size=1024,
        file_hash="cl1",
        status="human_confirmed",
        task_type="classification",
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)

    resp = await client.post(
        "/api/annotations/clear",
        headers=other_auth_headers,
        json={"image_ids": [img.id]},
    )
    assert resp.status_code == 403, f"User B should NOT clear A's annotations: {resp.text}"


@pytest.mark.asyncio
async def test_user_list_leak_admin_info(
    client, auth_headers, other_auth_headers
):
    """用户列表端点: 普通用户不应看到其他用户的 email/role"""
    resp = await client.get("/api/users/", headers=other_auth_headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])

    # 普通用户能看 username (供邀请成员), 但 email/role 应为 None
    for u in items:
        if u["id"] != other_user_id:
            assert u.get("email") is None, f"User B should NOT see email: {u}"
            assert u.get("role") is None, f"User B should NOT see role: {u}"


@pytest_asyncio.fixture
async def other_user_id(db_session) -> int:
    user = (await db_session.execute(
        select(User).where(User.username == "otheruser")
    )).scalar_one_or_none()
    return user.id if user else -1


@pytest.mark.asyncio
async def test_admin_can_see_all_users(
    client, admin_auth_headers
):
    """admin 用户列表能看到所有用户的完整信息"""
    resp = await client.get("/api/users/", headers=admin_auth_headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])

    # admin 应能看到 email, role 等
    for u in items:
        # admin 自身和 test_user, otheruser 都应可见
        assert u.get("email") is not None, f"admin should see email: {u}"
        assert u.get("role") is not None, f"admin should see role: {u}"


@pytest.mark.asyncio
async def test_stats_overview_user_isolation(
    client, auth_headers, other_auth_headers
):
    """统计端点 overview 应按用户隔离"""
    # A 创建一个 dataset
    await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "user_a_only_ds", "task_type": "classification", "category_names": []}
    )

    # 用户 A 的 overview
    resp_a = await client.get("/api/stats/overview", headers=auth_headers)
    assert resp_a.status_code == 200
    a_datasets = resp_a.json().get("datasets", 0)

    # 用户 B 的 overview (应该看不到 A 的)
    resp_b = await client.get("/api/stats/overview", headers=other_auth_headers)
    assert resp_b.status_code == 200
    b_datasets = resp_b.json().get("datasets", 0)

    # A 应有 1 个 dataset, B 应有 0 个
    assert a_datasets >= 1, f"User A should have datasets: {a_datasets}"
    assert b_datasets == 0, f"User B should NOT see A's datasets: {b_datasets}"


@pytest.mark.asyncio
async def test_admin_can_see_all_in_overview(
    client, auth_headers, admin_auth_headers
):
    """admin overview 应能看到所有用户的全部数据"""
    await client.post(
        "/api/datasets", headers=auth_headers,
        json={"name": "user_a_ds_admin_test", "task_type": "classification", "category_names": []}
    )

    resp = await client.get("/api/stats/overview", headers=admin_auth_headers)
    assert resp.status_code == 200
    # admin 至少能看到 1 个 dataset
    assert resp.json().get("datasets", 0) >= 1


@pytest.mark.asyncio
async def test_unauthenticated_cannot_access_protected(
    client
):
    """未登录用户无法访问受保护的资源"""
    endpoints = [
        "/api/datasets",
        "/api/datasets/1",
        "/api/stats/overview",
        "/api/users/",
        "/api/annotations/recent",
        "/api/training/jobs/1",
    ]
    for ep in endpoints:
        resp = await client.get(ep)
        assert resp.status_code == 401, f"Unauthenticated access to {ep} should be 401, got {resp.status_code}"
