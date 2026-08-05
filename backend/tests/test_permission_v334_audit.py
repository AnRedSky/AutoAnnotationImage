"""
v3.3.4 权限审查整改 - 安全测试 (DB 隔离部分)
============================================

对应权限审查报告 (docs/permissions-audit-2026-08-05.md) 中标记的 P0/P1 风险:
  P0-1: admin 跨团队数据穿透 → assert_can_access_dataset / assert_can_share_to_team
        现仅 super_admin 可绕过, regular admin 仍受团队隔离约束
  P0-2: start_existing_training_job 缺少数据集访问权限校验 → 已加
  P1-4: list_team_activities admin 旁路 → 已收紧为仅 super_admin
  P1-5: _assert_can_manage owner 伪造 TeamMember → 改为返回 None

注: P0-3 (change_user_role token 吊销) 测试在
    test_permission_v334_token_revocation.py — 该测试需要 Redis 模拟
    (decode_token 会查 Redis 吊销列表, 测试环境需 fakeredis)

测试矩阵 (DB 隔离):
  T01: super_admin 可访问任何团队的 dataset
  T02: regular admin (User.role=admin) 受团队隔离约束 (新行为)
  T03: owner 可访问自己 dataset
  T04: 普通成员按团队角色访问 dataset
  T05: 非成员不可访问团队 dataset
  T06: viewer 角色写入受拒
  T07: super_admin 可分享任何 dataset 到任何团队
  T08: regular admin 不可分享他人 dataset 到团队
  T09: 普通用户 (non-admin) 启动他人 training job → 403
  T10: 普通用户 restart 别人 dataset_id → 403
  T13: regular admin 无法访问未加入的团队活动
  T14: super_admin 可访问任意团队活动
  T15: _assert_can_manage 对 owner 不伪造 TeamMember (返回 None)
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
from app.middleware.security.security import hash_password


# ============== 通用 Fixtures ==============

@pytest_asyncio.fixture
async def super_admin_user(db_session: AsyncSession) -> User:
    """平台超级管理员 (User.role='super_admin') — 唯一可绕过团队隔离的角色"""
    user = User(
        username="v334_super",
        email="super334@example.com",
        password_hash=hash_password("superpass123"),
        role="super_admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def regular_admin_user(db_session: AsyncSession) -> User:
    """业务管理员 (User.role='admin') — v3.3.4 起受团队隔离约束 (新行为)"""
    user = User(
        username="v334_admin",
        email="admin334@example.com",
        password_hash=hash_password("adminpass334"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def alice(db_session: AsyncSession) -> User:
    """alice: 团队 A owner + manager"""
    user = User(
        username="v334_alice",
        email="alice334@example.com",
        password_hash=hash_password("alicepass334"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession) -> User:
    """bob: 团队 A 普通 editor"""
    user = User(
        username="v334_bob",
        email="bob334@example.com",
        password_hash=hash_password("bobpass334"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def carol(db_session: AsyncSession) -> User:
    """carol: 团队 B owner + manager (完全独立的团队, 测试跨团队数据隔离)"""
    user = User(
        username="v334_carol",
        email="carol334@example.com",
        password_hash=hash_password("carolpass334"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def team_a(db_session: AsyncSession, alice: User) -> Team:
    """alice 创建的团队 A"""
    team = Team(
        name="TeamA",
        slug="team-a-v334",
        owner_id=alice.id,
        max_members=20,
    )
    db_session.add(team)
    await db_session.commit()
    await db_session.refresh(team)
    # alice 自动加入为 manager
    member = TeamMember(team_id=team.id, user_id=alice.id, role="manager")
    db_session.add(member)
    await db_session.commit()
    return team


@pytest_asyncio.fixture
async def team_b(db_session: AsyncSession, carol: User) -> Team:
    """carol 创建的团队 B (alice/bob/admin 都不是成员)"""
    team = Team(
        name="TeamB",
        slug="team-b-v334",
        owner_id=carol.id,
        max_members=20,
    )
    db_session.add(team)
    await db_session.commit()
    await db_session.refresh(team)
    member = TeamMember(team_id=team.id, user_id=carol.id, role="manager")
    db_session.add(member)
    await db_session.commit()
    return team


@pytest_asyncio.fixture
async def bob_in_team_a(db_session: AsyncSession, team_a: Team, bob: User) -> TeamMember:
    """bob 加入团队 A 为 editor"""
    member = TeamMember(team_id=team_a.id, user_id=bob.id, role="editor")
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(member)
    return member


@pytest_asyncio.fixture
async def team_a_dataset(db_session: AsyncSession, team_a: Team, alice: User) -> Dataset:
    """alice 拥有 + 共享给团队 A 的 dataset"""
    ds = Dataset(
        name="team_a_shared",
        task_type="classification",
        owner_id=alice.id,
        team_id=team_a.id,
        status="draft",
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    return ds


@pytest_asyncio.fixture
async def carol_dataset(db_session: AsyncSession, carol: User) -> Dataset:
    """carol 拥有的个人 dataset, alice/bob/admin 都不可见"""
    ds = Dataset(
        name="carol_private",
        task_type="classification",
        owner_id=carol.id,
        team_id=None,
        status="draft",
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    return ds


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"Login {username} failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ============== P0-1: admin 跨团队数据穿透修复 ==============

@pytest.mark.asyncio
async def test_super_admin_can_access_any_team_dataset(
    client: AsyncClient, db_session, super_admin_user, team_a_dataset
):
    """T01: super_admin 可访问任意团队的 dataset (合规审计场景)

    这是预期行为: super_admin 是平台级管理角色, 需要能看跨团队数据用于审计.
    """
    headers = await _login(client, "v334_super", "superpass123")
    resp = await client.get(f"/api/datasets/{team_a_dataset.id}", headers=headers)
    assert resp.status_code == 200, f"super_admin should access any dataset: {resp.text}"


@pytest.mark.asyncio
async def test_regular_admin_blocked_by_team_isolation(
    client: AsyncClient, db_session, regular_admin_user, team_a_dataset
):
    """T02: regular admin (User.role='admin') 受团队隔离约束 (v3.3.4 新行为)

    旧行为: is_admin() 返回 True → 直接通过 → 越权访问
    新行为: 仅 super_admin 可绕过; regular admin 必须有团队成员关系
    """
    headers = await _login(client, "v334_admin", "adminpass334")
    # regular admin 不是 team_a 成员, 不应能看到 team_a 共享的 dataset
    resp = await client.get(f"/api/datasets/{team_a_dataset.id}", headers=headers)
    assert resp.status_code == 403, (
        f"regular admin should NOT access team's shared dataset (v3.3.4 fix). "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_owner_can_access_own_dataset(
    client: AsyncClient, db_session, alice, team_a_dataset
):
    """T03: owner 始终能访问自己 dataset (即使 admin 都不能改这条)"""
    headers = await _login(client, "v334_alice", "alicepass334")
    resp = await client.get(f"/api/datasets/{team_a_dataset.id}", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_team_member_can_access_shared_dataset(
    client: AsyncClient, db_session, bob, bob_in_team_a, team_a_dataset
):
    """T04: 团队成员 (editor) 可访问团队共享 dataset (只读/可写由 team role 决定)"""
    headers = await _login(client, "v334_bob", "bobpass334")
    resp = await client.get(f"/api/datasets/{team_a_dataset.id}", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_non_member_blocked_by_team_isolation(
    client: AsyncClient, db_session, carol, team_a_dataset
):
    """T05: 非成员不可访问他人团队 dataset (常规跨用户隔离)"""
    # carol 不在 team_a, 也不 own 该 dataset
    headers = await _login(client, "v334_carol", "carolpass334")
    resp = await client.get(f"/api/datasets/{team_a_dataset.id}", headers=headers)
    assert resp.status_code == 403


# ============== P0-2: start_existing_training_job 权限校验 ==============

@pytest.mark.asyncio
async def test_other_user_cannot_restart_training_job(
    client: AsyncClient, db_session, alice, bob, team_a_dataset
):
    """T09: 普通用户不能用他人 job_id 启动再训练

    旧行为: 无权限校验, 任何登录用户可重启任何 job
    新行为: 校验原 dataset 写权限 → bob 对 alice 的 dataset 无权限
    """
    # alice 创建一个已完成的 training job
    job = TrainingJob(
        user_id=alice.id,
        dataset_id=team_a_dataset.id,
        base_model="resnet50",
        model_name="alice_model_v1",
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

    # bob 尝试重启 alice 的 job
    bob_headers = await _login(client, "v334_bob", "bobpass334")
    resp = await client.post(
        f"/api/training/jobs/{job.id}/start?mode=restart",
        headers=bob_headers,
    )
    assert resp.status_code == 403, (
        f"bob should NOT restart alice's job (v3.3.4 fix). "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_owner_can_restart_own_job(
    client: AsyncClient, db_session, alice, team_a_dataset
):
    """T09-b: owner 可正常重启自己的 job (确保修复不影响正常路径)
    注: 测试环境 in-memory SQLite, _create_pending_restart_job 内部
    使用 AsyncSessionLocal (production engine), 与 test session 不同的 in-memory DB.
    mock 该函数返回预生成的 job_id, 跳过实际 INSERT.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    job = TrainingJob(
        user_id=alice.id,
        dataset_id=team_a_dataset.id,
        base_model="resnet50",
        model_name="alice_model_v1",
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

    alice_headers = await _login(client, "v334_alice", "alicepass334")

    # mock 两处以避免依赖真实 broker 和 production engine 的 in-memory DB:
    # 1) _create_pending_restart_job: 跳过 INSERT (test 环境下 prod engine 无表)
    #    原函数是 async def, 必须用 AsyncMock 否则 await 会 TypeError
    # 2) train_model_task.apply_async: 跳过 Celery broker 调用
    mock_task = MagicMock()
    mock_task.id = "fake-celery-id-for-v334-test"
    with patch(
        "app.tasks.api.training.start._create_pending_restart_job",
        new=AsyncMock(return_value=99999),
    ), patch(
        "app.tasks.api.training.start.train_model_task.apply_async",
        return_value=mock_task,
    ):
        resp = await client.post(
            f"/api/training/jobs/{job.id}/start?mode=restart",
            headers=alice_headers,
        )

    # 权限+资源校验通过后, 应返回 200 (mock 后无 broker/DB 依赖)
    assert resp.status_code == 200, (
        f"owner should be able to restart own job. "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== P0-3: change_user_role token 吊销 (需要 fakeredis, 拆分到独立文件) ==============
# T11/T12 见 test_permission_v334_token_revocation.py


# ============== P1-4: list_team_activities admin 旁路 ==============

@pytest.mark.asyncio
async def test_regular_admin_cannot_view_outsider_team_activities(
    client: AsyncClient, db_session, regular_admin_user, team_a
):
    """T13: regular admin 不能查看未加入团队的活动 feed

    旧行为: admin 旁路 _get_member_or_403 → 看到任何团队活动
    新行为: 仅 super_admin 可旁路; regular admin 需有成员关系
    """
    # regular admin 不是 team_a 成员
    assert team_a.owner_id != regular_admin_user.id
    headers = await _login(client, "v334_admin", "adminpass334")
    resp = await client.get(f"/api/teams/{team_a.id}/activities", headers=headers)
    assert resp.status_code == 403, (
        f"regular admin should NOT view outsider team activities (v3.3.4 fix). "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_super_admin_can_view_any_team_activities(
    client: AsyncClient, db_session, super_admin_user, team_a
):
    """T14: super_admin 可查看任意团队活动 (审计场景保留)"""
    headers = await _login(client, "v334_super", "superpass123")
    resp = await client.get(f"/api/teams/{team_a.id}/activities", headers=headers)
    assert resp.status_code == 200, (
        f"super_admin should view any team activities: {resp.text}"
    )


# ============== P1-5: _assert_can_manage owner 伪造 TeamMember ==============

@pytest.mark.asyncio
async def test_owner_can_manage_team_without_fake_member(
    client: AsyncClient, db_session, alice, team_a, team_a_dataset
):
    """T15: owner 仍可管理自己团队 (即使不在 team_member 表中)

    验证 _assert_can_manage 修复不影响正常 owner 路径:
    旧实现会构造游离 TeamMember 对象, 可能产生悬挂引用;
    新实现仅返回 None 由调用方处理 owner 语义.

    用 PATCH /api/teams/{id} 端点触发 _assert_can_manage (需 manager/owner).
    owner 身份应通过校验, 即使不在 team_member 表中.
    """
    # 先删除 alice 的 TeamMember 记录, 模拟"owner 不在 member 表"的边缘情况
    from sqlalchemy import delete
    await db_session.execute(
        delete(TeamMember).where(
            TeamMember.team_id == team_a.id,
            TeamMember.user_id == alice.id,
        )
    )
    await db_session.commit()
    await db_session.refresh(team_a)

    # alice 仍可编辑团队 (因为她是 owner, _assert_can_manage 允许)
    headers = await _login(client, "v334_alice", "alicepass334")
    # 不传 body (model 必填) → 用 query 更新 max_members
    resp = await client.patch(
        f"/api/teams/{team_a.id}?max_members=25",
        headers=headers,
    )
    # 关键: _assert_can_manage 通过, 没有 403
    # 后续业务校验 (比如 max_members 范围) 可能 422, 但不应 403
    assert resp.status_code != 403, (
        f"owner should pass _assert_can_manage check (v3.3.4 fix). "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== P2: 数据集共享权限 (assert_can_share_to_team) ==============

@pytest.mark.asyncio
async def test_regular_admin_cannot_share_others_dataset_to_team(
    client: AsyncClient, db_session, regular_admin_user, alice, team_a, carol_dataset
):
    """T16: regular admin 不能分享他人 dataset 到团队 (v3.3.4 加固)

    旧行为: admin 可绕过 owner 校验, 分享任何 dataset
    新行为: regular admin 受 assert_can_share_to_team 约束, 必须为 dataset owner
    """
    # carol_dataset 是 carol 的私有 dataset, regular_admin 既非 owner 也非 super_admin
    assert carol_dataset.owner_id != regular_admin_user.id

    headers = await _login(client, "v334_admin", "adminpass334")
    resp = await client.post(
        f"/api/teams/datasets/{carol_dataset.id}/share?team_id={team_a.id}",
        headers=headers,
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT share others' dataset (v3.3.4 fix). "
        f"Got: {resp.status_code} - {resp.text}"
    )


@pytest.mark.asyncio
async def test_super_admin_can_share_any_dataset_to_any_team(
    client: AsyncClient, db_session, super_admin_user, carol_dataset, team_a
):
    """T17: super_admin 可分享任何 dataset 到任何团队 (运维/合规场景保留)"""
    headers = await _login(client, "v334_super", "superpass123")
    resp = await client.post(
        f"/api/teams/datasets/{carol_dataset.id}/share?team_id={team_a.id}",
        headers=headers,
    )
    assert resp.status_code == 200, (
        f"super_admin should share any dataset to any team: {resp.text}"
    )


@pytest.mark.asyncio
async def test_non_member_cannot_share_dataset_to_team(
    client: AsyncClient, db_session, alice, team_b, carol_dataset
):
    """T18: 非目标团队成员, 即使是 dataset owner, 也不能分享到该团队

    业务规则: 仅目标团队成员可向该团队发起共享 (防止越权共享)
    carol 拥有 carol_dataset, 但不是 team_b (carol 自己建的) 的 owner
    → 我们用 alice (alice 不是 team_b 成员) 尝试分享自己 dataset 给 team_b
    """
    # 创建 alice 的 dataset (但 alice 不是 team_b 成员)
    alice_ds = Dataset(
        name="alice_private",
        task_type="classification",
        owner_id=alice.id,
        team_id=None,
        status="draft",
    )
    db_session.add(alice_ds)
    await db_session.commit()
    await db_session.refresh(alice_ds)

    headers = await _login(client, "v334_alice", "alicepass334")
    resp = await client.post(
        f"/api/teams/datasets/{alice_ds.id}/share?team_id={team_b.id}",
        headers=headers,
    )
    assert resp.status_code == 403, (
        f"non-member should NOT share dataset to team. "
        f"Got: {resp.status_code} - {resp.text}"
    )


# ============== P2: 模型激活权限 ==============

@pytest.mark.asyncio
async def test_other_user_cannot_activate_model_on_others_dataset(
    client: AsyncClient, db_session, alice, bob, team_a_dataset
):
    """T19: 普通用户不能激活他人 dataset 上的模型

    业务规则: 模型激活 = 对 dataset 的写操作, 需校验 dataset 写权限
    bob 不是 team_a 成员, 不能激活 alice 团队 dataset 上的模型
    """
    from app.tasks.model.model_version import ModelVersion
    # alice 在 team_a_dataset 上创建一个未激活的 model
    # 注: ModelVersion 没有 owner_id 字段, 权限通过所属 dataset 校验
    mv = ModelVersion(
        dataset_id=team_a_dataset.id,
        name="alice_mv_v1",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/fake_path.pth",  # 不需要真文件, 权限检查先于文件操作
        is_active=False,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)

    # bob 不是 team_a 成员, 尝试激活
    bob_headers = await _login(client, "v334_bob", "bobpass334")
    resp = await client.post(
        f"/api/models/{mv.id}/activate",
        headers=bob_headers,
    )
    assert resp.status_code == 403, (
        f"bob should NOT activate model on alice's team dataset. "
        f"Got: {resp.status_code} - {resp.text}"
    )
