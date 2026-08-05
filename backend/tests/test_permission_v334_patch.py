"""
v3.3.4-PATCH + v3.3.5-PERMISSION-REWRITE - 安全测试 (list 接口 + 单条操作 admin 旁路)
================================================================================

对应权限审查报告 (docs/permissions-audit-2026-08-05.md) 中标记的 P0/P1 风险:
  P0-2: list_training_jobs admin 旁路 → 仅 super_admin 看全部
  P0-3: list_models / get_model_detail admin 旁路 → 仅 super_admin 看全部
  P0-4: recent_annotations admin 旁路 → 仅 super_admin 看全系统
  P0-5: training jobs 单条操作 (cancel/pause/delete/error) admin 旁路
  P0-6: model delete/activate 越权 → regular admin 受 dataset 权限约束
  P1:   训练日志/进度 (training/detection/segmentation) admin 旁路

测试矩阵 (PATCH):
  T-P01: regular admin 调用 list_datasets 看不到其他团队 dataset
  T-P02: super_admin 调用 list_datasets 严格按 owner/team 过滤 (v3.3.5)
  T-P03: regular admin 调用 list_training_jobs 仅看自己+团队共享
  T-P04: super_admin 调用 list_training_jobs 也仅看自己+团队共享 (v3.3.5)
  T-P05: regular admin 调用 list_models 仅看自己+团队共享
  T-P06: super_admin 调用 list_models 也仅看自己+团队共享 (v3.3.5)
  T-P07: regular admin 调用 list_active_models 受 dataset 可见性约束
  T-P08: regular admin 调用 get_model_detail 失败 (别人的 model)
  T-P09: regular admin 调用 recent_annotations 受 dataset 可见性约束
  T-P10: super_admin 调用 recent_annotations 也仅看自己可见 dataset 的标注 (v3.3.5)
  T-P11: regular admin 调用 cancel_training_job 失败 (非 own)
  T-P12: regular admin 调用 pause_training_job 失败 (非 own)
  T-P13: regular admin 调用 get_training_log 失败 (非 own)
  T-P14: regular admin 调用 get_training_error 失败 (非 own)
  T-P15: regular admin 调用 delete_training_job 失败 (非 own)
  T-P16: regular admin 调用 delete_model 失败 (别人的 model)
  T-P17: regular admin 调用 deactivate_model 失败 (别人的 model, 孤儿)
  T-P18: owner 仍可正常操作自己的 job/model (确保修复不影响正常路径)

v3.3.5 重大变化:
  - 所有数据级 list 接口对 super_admin 同样按 owner/team 过滤
  - 越权测试中, super_admin 也不能看任何不属于自己的数据
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember
from app.tasks.model.dataset import Dataset
from app.tasks.model.training_job import TrainingJob
from app.tasks.model.model_version import ModelVersion
from app.middleware.security.security import hash_password


# ============== 通用 Fixtures ==============

@pytest_asyncio.fixture
async def super_admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="patch_super",
        email="patch_super@example.com",
        password_hash=hash_password("patchsuper123"),
        role="super_admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def regular_admin_user(db_session: AsyncSession) -> User:
    """v3.3.4 起受团队隔离约束的业务管理员"""
    user = User(
        username="patch_admin",
        email="patch_admin@example.com",
        password_hash=hash_password("patchadmin123"),
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
        username="patch_alice",
        email="patch_alice@example.com",
        password_hash=hash_password("patchalice123"),
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
        username="patch_bob",
        email="patch_bob@example.com",
        password_hash=hash_password("patchbob123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def carol(db_session: AsyncSession) -> User:
    """carol: 完全独立, 不在 alice 团队"""
    user = User(
        username="patch_carol",
        email="patch_carol@example.com",
        password_hash=hash_password("patchcarol123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def team_a(db_session: AsyncSession, alice: User) -> Team:
    team = Team(
        name="PatchTeamA",
        slug="patch-team-a",
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
async def bob_in_team_a(db_session: AsyncSession, team_a: Team, bob: User) -> TeamMember:
    member = TeamMember(team_id=team_a.id, user_id=bob.id, role="editor")
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(member)
    return member


@pytest_asyncio.fixture
async def alice_dataset(db_session: AsyncSession, alice: User) -> Dataset:
    ds = Dataset(
        name="alice_dataset_patch",
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
    """carol 的私有 dataset, alice/bob/admin 都不可见"""
    ds = Dataset(
        name="carol_dataset_patch",
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
async def alice_training_job(
    db_session: AsyncSession, alice: User, alice_dataset: Dataset
) -> TrainingJob:
    job = TrainingJob(
        user_id=alice.id,
        dataset_id=alice_dataset.id,
        base_model="resnet50",
        model_name="alice_job_patch",
        task_type="classification",
        epochs=1,
        batch_size=2,
        learning_rate=1e-4,
        state="SUCCESS",
        pretrain_mode="from_scratch",
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
        name="alice_mv_patch",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/fake_alice_mv.pth",
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


# ============== P0-2 / T-P03: list_training_jobs ==============

@pytest.mark.asyncio
async def test_regular_admin_list_training_jobs_excludes_outsider(
    client: AsyncClient, db_session, regular_admin_user, alice_training_job, carol_training_job_factory
):
    """T-P03: regular admin 调用 list_training_jobs 看不到其他用户的 job

    carol_training_job_factory: 创建 carol 拥有但 admin 不可见的 training job
    """
    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.get("/api/training/jobs", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    # regular admin 看不到任何 training job (因为没有 own + 没加入任何团队)
    job_ids = [j["id"] for j in items]
    assert carol_training_job_factory.id not in job_ids, (
        f"regular admin should NOT see carol's job (v3.3.4-PATCH fix). "
        f"Got jobs: {job_ids}"
    )


@pytest_asyncio.fixture
async def carol_training_job_factory(
    db_session: AsyncSession, carol: User, carol_dataset: Dataset
) -> TrainingJob:
    """carol 的 training job, admin 不可见"""
    job = TrainingJob(
        user_id=carol.id,
        dataset_id=carol_dataset.id,
        base_model="resnet50",
        model_name="carol_job_patch",
        task_type="classification",
        epochs=1,
        batch_size=2,
        learning_rate=1e-4,
        state="SUCCESS",
        pretrain_mode="from_scratch",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_super_admin_list_training_jobs_respects_ownership(
    client: AsyncClient, db_session, super_admin_user, alice_training_job, carol_training_job_factory
):
    """T-P04 (v3.3.5): super_admin 调用 list_training_jobs 也仅看自己+团队共享

    v3.3.5-PERMISSION-REWRITE: super_admin 不再有数据级全局旁路,
    任何角色 (含 super_admin) 均按 owner/team 过滤.
    """
    headers = await _login(client, "patch_super", "patchsuper123")
    resp = await client.get("/api/training/jobs", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    job_ids = [j["id"] for j in items]
    # super_admin 没有 own + 没加入任何团队 → 应看不到任何 job
    assert alice_training_job.id not in job_ids, (
        f"super_admin should NOT see alice's job (v3.3.5). "
        f"Got jobs: {job_ids}"
    )
    assert carol_training_job_factory.id not in job_ids, (
        f"super_admin should NOT see carol's job (v3.3.5). "
        f"Got jobs: {job_ids}"
    )


# ============== P0-3 / T-P05: list_models ==============

@pytest.mark.asyncio
async def test_regular_admin_list_models_excludes_outsider(
    client: AsyncClient, db_session, regular_admin_user, alice_model_version, carol_dataset
):
    """T-P05: regular admin 调用 list_models 看不到其他用户的 model"""
    # 创建 carol 的 model
    carol_mv = ModelVersion(
        dataset_id=carol_dataset.id,
        name="carol_mv_patch",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/fake_carol_mv.pth",
        is_active=False,
    )
    db_session.add(carol_mv)
    await db_session.commit()
    await db_session.refresh(carol_mv)

    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.get("/api/models", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    mv_ids = [m["id"] for m in items]
    assert carol_mv.id not in mv_ids, (
        f"regular admin should NOT see carol's model. Got: {mv_ids}"
    )


@pytest.mark.asyncio
async def test_super_admin_list_models_respects_ownership(
    client: AsyncClient, db_session, super_admin_user, alice_model_version, carol_dataset
):
    """T-P06 (v3.3.5): super_admin 调用 list_models 也仅看自己可见的

    v3.3.5: super_admin 不再有 model 列表的数据级旁路.
    """
    carol_mv = ModelVersion(
        dataset_id=carol_dataset.id,
        name="carol_mv_super",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/fake_carol_mv2.pth",
        is_active=False,
    )
    db_session.add(carol_mv)
    await db_session.commit()

    headers = await _login(client, "patch_super", "patchsuper123")
    resp = await client.get("/api/models", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    mv_ids = [m["id"] for m in items]
    # super_admin 不属于任何 dataset 的 owner/team → 都看不到
    assert alice_model_version.id not in mv_ids, (
        f"super_admin should NOT see alice's model (v3.3.5). Got: {mv_ids}"
    )
    assert carol_mv.id not in mv_ids, (
        f"super_admin should NOT see carol's model (v3.3.5). Got: {mv_ids}"
    )


# ============== P0-3 / T-P07: list_active_models ==============

@pytest.mark.asyncio
async def test_regular_admin_list_active_models_excludes_outsider(
    client: AsyncClient, db_session, regular_admin_user, alice_dataset
):
    """T-P07: regular admin 调用 list_active_models 受 dataset 可见性约束"""
    # 创建 carol 的 active model
    carol = User(
        username="patch_carol_act",
        email="patch_carol_act@example.com",
        password_hash=hash_password("patchcarol123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(carol)
    await db_session.commit()
    carol_ds = Dataset(
        name="carol_act_ds",
        task_type="classification",
        owner_id=carol.id,
        team_id=None,
        status="draft",
    )
    db_session.add(carol_ds)
    await db_session.commit()
    carol_mv = ModelVersion(
        dataset_id=carol_ds.id,
        name="carol_active_mv",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/fake_carol_active.pth",
        is_active=True,
    )
    db_session.add(carol_mv)
    await db_session.commit()

    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.get("/api/models/active", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    mv_ids = [m["id"] for m in items]
    assert carol_mv.id not in mv_ids, (
        f"regular admin should NOT see carol's active model. Got: {mv_ids}"
    )


# ============== P0-3 / T-P08: get_model_detail ==============

@pytest.mark.asyncio
async def test_regular_admin_get_model_detail_blocked_by_dataset_isolation(
    client: AsyncClient, db_session, regular_admin_user, alice_model_version
):
    """T-P08: regular admin 调用 get_model_detail 失败 (无 dataset 权限)"""
    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.get(
        f"/api/models/{alice_model_version.id}/detail", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT see alice's model detail. "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== P0-4 / T-P09: recent_annotations ==============

@pytest.mark.asyncio
async def test_regular_admin_recent_annotations_excludes_outsider(
    client: AsyncClient, db_session, regular_admin_user, alice_dataset
):
    """T-P09: regular admin 调用 recent_annotations 受 dataset 可见性约束"""
    from app.tasks.model.annotation_log import AnnotationLog
    from app.tasks.model.image import Image

    # 创建 carol + carol_ds + image + annotation
    carol = User(
        username="patch_carol_recent",
        email="patch_carol_recent@example.com",
        password_hash=hash_password("patchcarol123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(carol)
    await db_session.commit()
    carol_ds = Dataset(
        name="carol_recent_ds",
        task_type="classification",
        owner_id=carol.id,
        team_id=None,
        status="draft",
    )
    db_session.add(carol_ds)
    await db_session.commit()
    carol_img = Image(
        dataset_id=carol_ds.id,
        filename="carol_img.png",
        storage_path="/tmp/carol_img.png",
        file_hash="fake_hash_recent",
        status="ai_labeled",
    )
    db_session.add(carol_img)
    await db_session.commit()
    carol_log = AnnotationLog(
        image_id=carol_img.id,
        user_id=carol.id,
        action="confirm",
    )
    db_session.add(carol_log)
    await db_session.commit()

    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.get("/api/annotations/recent", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    log_ids = [log["id"] for log in items]
    assert carol_log.id not in log_ids, (
        f"regular admin should NOT see carol's annotation log. "
        f"Got: {log_ids}"
    )


@pytest.mark.asyncio
async def test_super_admin_recent_annotations_respects_ownership(
    client: AsyncClient, db_session, super_admin_user, alice_dataset
):
    """T-P10 (v3.3.5): super_admin 调用 recent_annotations 也仅看自己可见 dataset 的标注

    v3.3.5: super_admin 不再有 recent annotations 的全局旁路.
    """
    from app.tasks.model.annotation_log import AnnotationLog
    from app.tasks.model.image import Image

    carol = User(
        username="patch_carol_recent2",
        email="patch_carol_recent2@example.com",
        password_hash=hash_password("patchcarol123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(carol)
    await db_session.commit()
    carol_ds = Dataset(
        name="carol_recent2_ds",
        task_type="classification",
        owner_id=carol.id,
        team_id=None,
        status="draft",
    )
    db_session.add(carol_ds)
    await db_session.commit()
    carol_img = Image(
        dataset_id=carol_ds.id,
        filename="carol_img2.png",
        storage_path="/tmp/carol_img2.png",
        file_hash="fake_hash_recent2",
        status="ai_labeled",
    )
    db_session.add(carol_img)
    await db_session.commit()
    carol_log = AnnotationLog(
        image_id=carol_img.id,
        user_id=carol.id,
        action="confirm",
    )
    db_session.add(carol_log)
    await db_session.commit()

    headers = await _login(client, "patch_super", "patchsuper123")
    resp = await client.get("/api/annotations/recent", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    log_ids = [log["id"] for log in items]
    # super_admin 不属于 carol_ds 的 owner/team → 看不到 carol 的标注
    assert carol_log.id not in log_ids, (
        f"super_admin should NOT see carol's annotation log (v3.3.5). "
        f"Got: {log_ids}"
    )


# ============== P0-5 / T-P11~T-P15: training job 单条操作 ==============

@pytest.mark.asyncio
async def test_regular_admin_cannot_cancel_others_job(
    client: AsyncClient, db_session, regular_admin_user, alice_training_job
):
    """T-P11: regular admin 调用 cancel_training_job 失败 (非 own)"""
    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.post(
        f"/api/training/jobs/{alice_training_job.id}/cancel", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT cancel alice's job. "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_regular_admin_cannot_pause_others_job(
    client: AsyncClient, db_session, regular_admin_user, alice_training_job
):
    """T-P12: regular admin 调用 pause_training_job 失败 (非 own)"""
    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.post(
        f"/api/training/jobs/{alice_training_job.id}/pause", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT pause alice's job. "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_regular_admin_cannot_view_others_job_log(
    client: AsyncClient, db_session, regular_admin_user, alice_training_job
):
    """T-P13: regular admin 调用 get_training_log 失败 (非 own)"""
    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.get(
        f"/api/training/jobs/{alice_training_job.id}/log", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT view alice's job log. "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_regular_admin_cannot_view_others_job_error(
    client: AsyncClient, db_session, regular_admin_user, alice_training_job
):
    """T-P14: regular admin 调用 get_training_error 失败 (非 own)"""
    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.post(
        f"/api/training/jobs/{alice_training_job.id}/error", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT view alice's job error. "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_regular_admin_cannot_delete_others_job(
    client: AsyncClient, db_session, regular_admin_user, alice_training_job
):
    """T-P15: regular admin 调用 delete_training_job 失败 (非 own)"""
    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.delete(
        f"/api/training/jobs/{alice_training_job.id}", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT delete alice's job. "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== P0-6 / T-P16~T-P17: model delete/activate ==============

@pytest.mark.asyncio
async def test_regular_admin_cannot_delete_others_model(
    client: AsyncClient, db_session, regular_admin_user, alice_model_version
):
    """T-P16: regular admin 调用 delete_model 失败 (别人的 model)"""
    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.delete(
        f"/api/models/{alice_model_version.id}", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT delete alice's model. "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_regular_admin_cannot_deactivate_orphan_model(
    client: AsyncClient, db_session, regular_admin_user
):
    """T-P17: regular admin 调用 deactivate_model 失败 (孤儿 model)

    孤儿 model: dataset_id=None, 旧逻辑 regular admin 可操作
    新逻辑: 仅 super_admin 可操作
    """
    orphan_mv = ModelVersion(
        dataset_id=None,
        name="orphan_mv_patch",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/orphan.pth",
        is_active=True,
    )
    db_session.add(orphan_mv)
    await db_session.commit()
    await db_session.refresh(orphan_mv)

    headers = await _login(client, "patch_admin", "patchadmin123")
    resp = await client.post(
        f"/api/models/{orphan_mv.id}/deactivate", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT deactivate orphan model. "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== T-P18: owner 仍可正常操作 (确保修复不影响正常路径) ==============

@pytest.mark.asyncio
async def test_owner_can_still_cancel_own_job(
    client: AsyncClient, db_session, alice, alice_training_job, fake_redis
):
    """T-P18: owner 仍可正常 cancel 自己的 job"""
    headers = await _login(client, "patch_alice", "patchalice123")
    resp = await client.post(
        f"/api/training/jobs/{alice_training_job.id}/cancel", headers=headers
    )
    # SUCCESS 状态不允许 cancel (业务限制), 但权限校验已通过
    # 返回 success=False 状态提示, 但不应 403
    assert resp.status_code == 200, (
        f"owner should pass permission check on own job. "
        f"Got: {resp.status_code} - {resp.text}"
    )
    body = resp.json()
    assert body.get("state") == "SUCCESS", (
        f"Expected job state 'SUCCESS' (cannot cancel terminal job), got: {body}"
    )


@pytest.mark.asyncio
async def test_owner_can_view_own_job_log(
    client: AsyncClient, db_session, alice, alice_training_job
):
    """T-P18-b: owner 仍可正常查看自己的 job log"""
    headers = await _login(client, "patch_alice", "patchalice123")
    resp = await client.get(
        f"/api/training/jobs/{alice_training_job.id}/log", headers=headers
    )
    assert resp.status_code == 200, (
        f"owner should view own job log. "
        f"Got: {resp.status_code} - {resp.text}"
    )
