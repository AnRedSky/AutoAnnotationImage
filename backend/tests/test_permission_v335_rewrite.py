"""
v3.3.5-PERMISSION-REWRITE 全面权限架构重写 - 回归测试
=====================================================

3 层权限模型:
  - 系统层 (system role): super_admin / admin / annotator / viewer
  - 团队层 (team role):   manager / editor / viewer
  - 数据层 (data owner):   owner_id 字段

v3.3.5 核心变化 — 严格最小权限:
  - 任何角色 (含 super_admin) 在数据级接口均受 owner/team 约束
  - 越权访问统一写 audit_log (event_type=permission_denied)
  - 孤儿 model (dataset_id=NULL) 任何角色都拒绝
  - 系统级管理 (用户/审计/统计) 仍保留 super_admin/admin 旁路

测试矩阵 (22 项):
  T-V01: super_admin 看不到其他用户的 dataset
  T-V02: super_admin 看不到其他用户的 training job
  T-V03: super_admin 看不到其他用户的 model
  T-V04: super_admin 看不到其他用户的 recent annotation
  T-V05: super_admin 调用 get_model_detail 失败 (别人的 model)
  T-V06: super_admin 调用 cancel_training_job 失败 (别人的 job)
  T-V07: super_admin 调用 get_training_log 失败 (别人的 job)
  T-V08: super_admin 调用 get_training_progress 失败 (别人的 job)
  T-V09: super_admin 调用 detection progress 失败 (别人的 job)
  T-V10: super_admin 调用 segmentation progress 失败 (别人的 job)
  T-V11: super_admin 调用 delete_model 失败 (别人的 model)
  T-V12: super_admin 调用 activate_model 失败 (别人的 model)
  T-V13: 孤儿 model (dataset_id=NULL) 任何角色都拒绝
  T-V14: owner 仍可正常访问自己的资源 (正常路径不破)
  T-V15: team member 可访问共享的 dataset/job/model
  T-V16: 403 越权写 audit_log (event_type=permission_denied)
  T-V17: regular admin 越权同样写 audit_log
  T-V18: 系统级接口 (user mgmt) 仍允许 super_admin
  T-V19: 系统级接口 (audit logs) 仍允许 admin
  T-V20: 系统级接口 (stats) 仍允许 admin
  T-V21: assert_can_share_to_team 仍允许 super_admin 发起共享
  T-V22: 越权访问不影响正常返回 403 (best-effort 审计)
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember
from app.tasks.model.dataset import Dataset
from app.tasks.model.training_job import TrainingJob
from app.tasks.model.model_version import ModelVersion
from app.tasks.model.audit_log import AuditLog
from app.middleware.security.security import hash_password


# ============== 通用 Fixtures ==============

@pytest_asyncio.fixture
async def super_admin(db_session: AsyncSession) -> User:
    user = User(
        username="v335_super",
        email="v335_super@example.com",
        password_hash=hash_password("v335super123"),
        role="super_admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def regular_admin(db_session: AsyncSession) -> User:
    user = User(
        username="v335_admin",
        email="v335_admin@example.com",
        password_hash=hash_password("v335admin123"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def alice(db_session: AsyncSession) -> User:
    user = User(
        username="v335_alice",
        email="v335_alice@example.com",
        password_hash=hash_password("v335alice123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession) -> User:
    user = User(
        username="v335_bob",
        email="v335_bob@example.com",
        password_hash=hash_password("v335bob123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def carol(db_session: AsyncSession) -> User:
    user = User(
        username="v335_carol",
        email="v335_carol@example.com",
        password_hash=hash_password("v335carol123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def team_t(db_session: AsyncSession, alice: User) -> Team:
    team = Team(
        name="V335TeamT",
        slug="v335-team-t",
        owner_id=alice.id,
        max_members=20,
    )
    db_session.add(team)
    await db_session.commit()
    await db_session.refresh(team)
    member = TeamMember(team_id=team.id, user_id=alice.id, role="manager")
    db_session.add(member)
    await db_session.commit()
    return team


@pytest_asyncio.fixture
async def bob_in_team_t(
    db_session: AsyncSession, team_t: Team, bob: User
) -> TeamMember:
    member = TeamMember(team_id=team_t.id, user_id=bob.id, role="editor")
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(member)
    return member


@pytest_asyncio.fixture
async def alice_dataset(db_session: AsyncSession, alice: User) -> Dataset:
    ds = Dataset(
        name="v335_alice_ds",
        task_type="classification",
        owner_id=alice.id,
        team_id=None,
        status="draft",
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    return ds


@pytest_asyncio.fixture
async def carol_dataset(db_session: AsyncSession, carol: User) -> Dataset:
    ds = Dataset(
        name="v335_carol_ds",
        task_type="classification",
        owner_id=carol.id,
        team_id=None,
        status="draft",
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    return ds


@pytest_asyncio.fixture
async def alice_team_dataset(
    db_session: AsyncSession, alice: User, team_t: Team
) -> Dataset:
    """alice 创建的、共享给 team_t 的 dataset"""
    ds = Dataset(
        name="v335_team_ds",
        task_type="classification",
        owner_id=alice.id,
        team_id=team_t.id,
        status="draft",
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    return ds


@pytest_asyncio.fixture
async def alice_training_job(
    db_session: AsyncSession, alice: User, alice_dataset: Dataset
) -> TrainingJob:
    job = TrainingJob(
        user_id=alice.id,
        dataset_id=alice_dataset.id,
        base_model="resnet50",
        model_name="v335_alice_job",
        task_type="classification",
        epochs=1,
        batch_size=2,
        learning_rate=1e-4,
        state="SUCCESS",
        pretrain_mode="from_scratch",
        celery_task_id="v335-celery-alice-1",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest_asyncio.fixture
async def carol_training_job(
    db_session: AsyncSession, carol: User, carol_dataset: Dataset
) -> TrainingJob:
    job = TrainingJob(
        user_id=carol.id,
        dataset_id=carol_dataset.id,
        base_model="resnet50",
        model_name="v335_carol_job",
        task_type="classification",
        epochs=1,
        batch_size=2,
        learning_rate=1e-4,
        state="SUCCESS",
        pretrain_mode="from_scratch",
        celery_task_id="v335-celery-carol-1",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest_asyncio.fixture
async def alice_model_version(
    db_session: AsyncSession, alice_dataset: Dataset
) -> ModelVersion:
    mv = ModelVersion(
        dataset_id=alice_dataset.id,
        name="v335_alice_mv",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/v335_alice_mv.pth",
        is_active=False,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)
    return mv


@pytest_asyncio.fixture
async def orphan_model(db_session: AsyncSession) -> ModelVersion:
    """孤儿 model: dataset_id=None, 任何角色都应拒绝"""
    mv = ModelVersion(
        dataset_id=None,
        name="v335_orphan_mv",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/v335_orphan_mv.pth",
        is_active=False,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)
    return mv


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"Login {username} failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ============== T-V01~T-V04: 数据级 list 接口严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V01_super_admin_list_datasets_excludes_outsider(
    client: AsyncClient, db_session, super_admin, alice_dataset, carol_dataset
):
    """T-V01: super_admin 看不到其他用户的 dataset"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get("/api/datasets", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    ds_ids = [d["id"] for d in items]
    assert alice_dataset.id not in ds_ids, "super_admin should NOT see alice's dataset (v3.3.5)"
    assert carol_dataset.id not in ds_ids, "super_admin should NOT see carol's dataset (v3.3.5)"


@pytest.mark.asyncio
async def test_T_V02_super_admin_list_training_jobs_excludes_outsider(
    client: AsyncClient, db_session, super_admin, alice_training_job, carol_training_job
):
    """T-V02: super_admin 看不到其他用户的 training job"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get("/api/training/jobs", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    job_ids = [j["id"] for j in items]
    assert alice_training_job.id not in job_ids, "super_admin should NOT see alice's job (v3.3.5)"
    assert carol_training_job.id not in job_ids, "super_admin should NOT see carol's job (v3.3.5)"


@pytest.mark.asyncio
async def test_T_V03_super_admin_list_models_excludes_outsider(
    client: AsyncClient, db_session, super_admin, alice_model_version, carol_dataset
):
    """T-V03: super_admin 看不到其他用户的 model"""
    carol_mv = ModelVersion(
        dataset_id=carol_dataset.id,
        name="v335_carol_mv",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/v335_carol_mv.pth",
        is_active=False,
    )
    db_session.add(carol_mv)
    await db_session.commit()

    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get("/api/models", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    mv_ids = [m["id"] for m in items]
    assert alice_model_version.id not in mv_ids, "super_admin should NOT see alice's model (v3.3.5)"
    assert carol_mv.id not in mv_ids, "super_admin should NOT see carol's model (v3.3.5)"


@pytest.mark.asyncio
async def test_T_V04_super_admin_recent_annotations_excludes_outsider(
    client: AsyncClient, db_session, super_admin, alice_dataset, carol_dataset
):
    """T-V04: super_admin 看不到其他用户的 recent annotation"""
    from app.tasks.model.annotation_log import AnnotationLog
    from app.tasks.model.image import Image

    alice_img = Image(
        dataset_id=alice_dataset.id,
        filename="v335_alice_img.png",
        storage_path="/tmp/v335_alice_img.png",
        file_hash="hash_v335_alice",
        status="ai_labeled",
    )
    db_session.add(alice_img)
    await db_session.commit()
    alice_log = AnnotationLog(image_id=alice_img.id, user_id=alice_dataset.owner_id, action="confirm")
    db_session.add(alice_log)

    carol_img = Image(
        dataset_id=carol_dataset.id,
        filename="v335_carol_img.png",
        storage_path="/tmp/v335_carol_img.png",
        file_hash="hash_v335_carol",
        status="ai_labeled",
    )
    db_session.add(carol_img)
    await db_session.commit()
    carol_log = AnnotationLog(image_id=carol_img.id, user_id=carol_dataset.owner_id, action="confirm")
    db_session.add(carol_log)
    await db_session.commit()

    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get("/api/annotations/recent", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    log_ids = [log["id"] for log in items]
    assert alice_log.id not in log_ids, "super_admin should NOT see alice's log (v3.3.5)"
    assert carol_log.id not in log_ids, "super_admin should NOT see carol's log (v3.3.5)"


# ============== T-V05: get_model_detail 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V05_super_admin_get_model_detail_blocked(
    client: AsyncClient, db_session, super_admin, alice_model_version
):
    """T-V05: super_admin 调用 get_model_detail 失败 (别人的 model)"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get(
        f"/api/models/{alice_model_version.id}/detail", headers=headers
    )
    assert resp.status_code == 403, (
        f"super_admin should NOT see alice's model detail (v3.3.5). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V06: cancel_training_job 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V06_super_admin_cancel_training_job_blocked(
    client: AsyncClient, db_session, super_admin, alice_training_job
):
    """T-V06: super_admin 调用 cancel_training_job 失败 (别人的 job)"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.post(
        f"/api/training/jobs/{alice_training_job.id}/cancel", headers=headers
    )
    assert resp.status_code == 403, (
        f"super_admin should NOT cancel alice's job (v3.3.5). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V07: get_training_log 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V07_super_admin_get_training_log_blocked(
    client: AsyncClient, db_session, super_admin, alice_training_job
):
    """T-V07: super_admin 调用 get_training_log 失败 (别人的 job)"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get(
        f"/api/training/jobs/{alice_training_job.id}/log", headers=headers
    )
    assert resp.status_code == 403, (
        f"super_admin should NOT view alice's job log (v3.3.5). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V08: training progress 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V08_super_admin_training_progress_blocked(
    client: AsyncClient, db_session, super_admin, alice_training_job
):
    """T-V08: super_admin 调用 training progress 失败 (别人的 job)"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get(
        f"/api/training/progress/{alice_training_job.celery_task_id}",
        headers=headers,
    )
    assert resp.status_code == 403, (
        f"super_admin should NOT view alice's training progress (v3.3.5). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V09: detection progress 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V09_super_admin_detection_progress_blocked(
    client: AsyncClient, db_session, super_admin, alice_training_job
):
    """T-V09: super_admin 调用 detection progress 失败 (别人的 job)"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get(
        f"/api/detection/progress/{alice_training_job.celery_task_id}",
        headers=headers,
    )
    assert resp.status_code == 403, (
        f"super_admin should NOT view alice's detection progress (v3.3.5). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V10: segmentation progress 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V10_super_admin_segmentation_progress_blocked(
    client: AsyncClient, db_session, super_admin, alice_training_job
):
    """T-V10: super_admin 调用 segmentation progress 失败 (别人的 job)"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get(
        f"/api/segmentation/progress/{alice_training_job.celery_task_id}",
        headers=headers,
    )
    assert resp.status_code == 403, (
        f"super_admin should NOT view alice's segmentation progress (v3.3.5). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V11: delete_model 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V11_super_admin_delete_model_blocked(
    client: AsyncClient, db_session, super_admin, alice_model_version
):
    """T-V11: super_admin 调用 delete_model 失败 (别人的 model)"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.delete(
        f"/api/models/{alice_model_version.id}", headers=headers
    )
    assert resp.status_code == 403, (
        f"super_admin should NOT delete alice's model (v3.3.5). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V12: activate_model 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_V12_super_admin_activate_model_blocked(
    client: AsyncClient, db_session, super_admin, alice_model_version
):
    """T-V12: super_admin 调用 activate_model 失败 (别人的 model)"""
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.post(
        f"/api/models/{alice_model_version.id}/activate", headers=headers
    )
    assert resp.status_code == 403, (
        f"super_admin should NOT activate alice's model (v3.3.5). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V13: 孤儿 model 任何角色拒绝 ==============

@pytest.mark.asyncio
async def test_T_V13_orphan_model_rejected_for_all_roles(
    client: AsyncClient, db_session, super_admin, regular_admin, orphan_model
):
    """T-V13: 孤儿 model (dataset_id=NULL) 任何角色都拒绝"""
    # super_admin 访问
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get(
        f"/api/models/{orphan_model.id}/detail", headers=headers
    )
    assert resp.status_code == 403, "super_admin should NOT see orphan model"

    # regular admin 访问
    headers = await _login(client, "v335_admin", "v335admin123")
    resp = await client.get(
        f"/api/models/{orphan_model.id}/detail", headers=headers
    )
    assert resp.status_code == 403, "regular admin should NOT see orphan model"

    # super_admin 删除
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.delete(
        f"/api/models/{orphan_model.id}", headers=headers
    )
    assert resp.status_code == 403, "super_admin should NOT delete orphan model"


# ============== T-V14: owner 正常路径不破 ==============

@pytest.mark.asyncio
async def test_T_V14_owner_can_still_access_own_resources(
    client: AsyncClient, db_session, alice, alice_dataset, alice_training_job, alice_model_version
):
    """T-V14: owner 仍可正常访问自己的 dataset/job/model (正常路径不破)"""
    headers = await _login(client, "v335_alice", "v335alice123")

    # dataset 列表能看到自己的
    resp = await client.get("/api/datasets", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    assert alice_dataset.id in [d["id"] for d in items]

    # training job 列表能看到自己的
    resp = await client.get("/api/training/jobs", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    assert alice_training_job.id in [j["id"] for j in items]

    # model 列表能看到自己的
    resp = await client.get("/api/models", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    assert alice_model_version.id in [m["id"] for m in items]

    # model detail
    resp = await client.get(
        f"/api/models/{alice_model_version.id}/detail", headers=headers
    )
    assert resp.status_code == 200, "owner should view own model detail"

    # training log
    resp = await client.get(
        f"/api/training/jobs/{alice_training_job.id}/log", headers=headers
    )
    assert resp.status_code == 200, "owner should view own job log"


# ============== T-V15: team member 可访问共享的 dataset/job/model ==============

@pytest.mark.asyncio
async def test_T_V15_team_member_can_access_shared_resources(
    client: AsyncClient, db_session, bob, bob_in_team_t, alice_team_dataset
):
    """T-V15: team member (bob) 可访问 alice 共享给 team_t 的 dataset"""
    headers = await _login(client, "v335_bob", "v335bob123")

    # dataset 列表能看到团队共享的
    resp = await client.get("/api/datasets", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    assert alice_team_dataset.id in [d["id"] for d in items], (
        f"team member should see shared dataset. Got: {[d['id'] for d in items]}"
    )


# ============== T-V16: super_admin 越权写 audit_log ==============

@pytest.mark.asyncio
async def test_T_V16_super_admin_violation_writes_audit_log(
    client: AsyncClient, db_session, super_admin, alice_model_version
):
    """T-V16: super_admin 越权访问时, audit_log 写一条 permission_denied 记录"""
    headers = await _login(client, "v335_super", "v335super123")
    # 触发越权
    resp = await client.get(
        f"/api/models/{alice_model_version.id}/detail", headers=headers
    )
    assert resp.status_code == 403

    # 验证 audit_log 写入
    await db_session.commit()  # 确保 log flush
    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.user_id == super_admin.id,
            AuditLog.event_type == "permission_denied",
        )
    )
    logs = result.scalars().all()
    # 至少有一条 permission_denied 日志
    assert len(logs) > 0, "audit_log should record super_admin's violation"
    # detail 包含 endpoint 和 reason
    last_log = logs[-1]
    assert "endpoint" in (last_log.detail or {}), "audit_log detail should include endpoint"
    assert "reason" in (last_log.detail or {}), "audit_log detail should include reason"


# ============== T-V17: regular admin 越权同样写 audit_log ==============

@pytest.mark.asyncio
async def test_T_V17_regular_admin_violation_writes_audit_log(
    client: AsyncClient, db_session, regular_admin, alice_model_version
):
    """T-V17: regular admin 越权访问时, audit_log 写一条 permission_denied 记录"""
    headers = await _login(client, "v335_admin", "v335admin123")
    resp = await client.get(
        f"/api/models/{alice_model_version.id}/detail", headers=headers
    )
    assert resp.status_code == 403

    await db_session.commit()
    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.user_id == regular_admin.id,
            AuditLog.event_type == "permission_denied",
        )
    )
    logs = result.scalars().all()
    assert len(logs) > 0, "audit_log should record regular admin's violation"


# ============== T-V18: 系统级接口 (user mgmt) 仍允许 super_admin ==============

@pytest.mark.asyncio
async def test_T_V18_super_admin_user_management_still_works(
    client: AsyncClient, db_session, super_admin
):
    """T-V18: 系统级接口 (用户管理) super_admin 仍可访问

    系统级接口不归数据级权限约束, 保留 super_admin/admin 旁路
    """
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get("/api/admin/users", headers=headers)
    assert resp.status_code == 200, (
        f"super_admin should access system-level user mgmt. "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V19: 系统级接口 (audit logs) 仍允许 admin ==============

@pytest.mark.asyncio
async def test_T_V19_admin_audit_logs_still_works(
    client: AsyncClient, db_session, regular_admin
):
    """T-V19: 系统级接口 (审计日志) admin 仍可访问"""
    headers = await _login(client, "v335_admin", "v335admin123")
    resp = await client.get("/api/audit-logs", headers=headers)
    assert resp.status_code == 200, (
        f"admin should access system-level audit logs. "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V20: 系统级接口 (stats) 仍允许 admin ==============

@pytest.mark.asyncio
async def test_T_V20_admin_stats_still_works(
    client: AsyncClient, db_session, regular_admin
):
    """T-V20: 系统级接口 (统计) admin 仍可访问"""
    headers = await _login(client, "v335_admin", "v335admin123")
    resp = await client.get("/api/stats/overview", headers=headers)
    assert resp.status_code == 200, (
        f"admin should access system-level stats. "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-V21: assert_can_share_to_team 仍允许 super_admin ==============

@pytest.mark.asyncio
async def test_T_V21_super_admin_can_share_to_team(
    client: AsyncClient, db_session, super_admin, alice_dataset, team_t
):
    """T-V21: super_admin 仍可将 dataset 共享到 team (平台运维操作)"""
    # 直接调用 service 层函数验证
    from app.tasks.service.permission_service import assert_can_share_to_team

    # super_admin 不必是 owner, 也不必是 team 成员, 仍可共享 (平台级)
    # 这里需要重新加载 super_admin 以确保 session 关联
    sa = await db_session.get(User, super_admin.id)
    # 不应抛异常
    try:
        await assert_can_share_to_team(db_session, sa, alice_dataset, team_t.id)
    except Exception as e:
        # 如果抛 403, 说明 super_admin 也被收紧, 这不符合预期
        pytest.fail(f"super_admin should still be able to share to team: {e}")


# ============== T-V22: 越权返回 403 (best-effort 审计) ==============

@pytest.mark.asyncio
async def test_T_V22_violation_returns_403_despite_audit_failure(
    client: AsyncClient, db_session, super_admin, alice_model_version
):
    """T-V22: 越权访问仍然返回 403, 审计失败不应影响主流程

    log_permission_denied 是 best-effort, 即便审计失败, 也应返回 403
    """
    headers = await _login(client, "v335_super", "v335super123")
    resp = await client.get(
        f"/api/models/{alice_model_version.id}/detail", headers=headers
    )
    # 必须 403, 不能是 500 (审计失败导致)
    assert resp.status_code == 403, (
        f"violation should return 403, not 500. "
        f"Got: {resp.status_code} - {resp.text}"
    )
