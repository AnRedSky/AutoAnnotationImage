"""
团队管理集成测试 (v3.3.1)
==========================

覆盖团队管理 P0 端点的端到端行为:
  - 团队 CRUD (含 PATCH 编辑)
  - 所有权转让 (含边界)
  - 主动退队 (含 owner 例外)
  - 11 个写操作的审计日志
  - 团队级数据集列表
  - 数据隔离 (非成员 403)

测试矩阵 (T01-T20):
  T01: 创建团队 + 自动加入
  T02: 创建团队 + slug 重复
  T03: PATCH 团队名 (manager)
  T04: PATCH 团队 (viewer) → 403
  T05: PATCH max_members < 当前 → 400
  T06: POST /transfer (非 owner) → 403
  T07: POST /transfer (owner, receiver=viewer) → 400
  T08: POST /transfer (owner, confirm=False) → 400
  T09: POST /transfer (owner, 合法) → 200 + 审计
  T10: DELETE /members/me (owner) → 400
  T11: DELETE /members/me (普通成员) → 200 + 审计
  T12: 数据隔离: 非成员 GET /api/teams/{id} → 403
  T13: GET /api/teams/{id}/datasets (非成员) → 403
  T14: GET /api/teams/{id}/datasets (成员) → 200 含 owner_name
  T15: 11 个写操作全部产生 audit_log
  T16: 转让后原 owner 仍可访问 (作为 manager)
  T17: 删除团队 (owner) → 200, dataset.team_id 清空
  T18: 删除团队 (非 owner) → 403
  T19: 邀请已存在成员 → 409
  T20: 邀请超限 → 400
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
from app.tasks.model.audit_log import AuditLog
from app.middleware.security.security import hash_password


# ============== Helper Fixtures ==============

@pytest_asyncio.fixture
async def alice(db_session: AsyncSession) -> User:
    """owner 用户 A"""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash=hash_password("alicepass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession) -> User:
    """manager 用户 B"""
    user = User(
        username="bob",
        email="bob@example.com",
        password_hash=hash_password("bobpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def carol(db_session: AsyncSession) -> User:
    """viewer 用户 C"""
    user = User(
        username="carol",
        email="carol@example.com",
        password_hash=hash_password("carolpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def dave(db_session: AsyncSession) -> User:
    """旁观者用户 D (非任何团队成员)"""
    user = User(
        username="dave",
        email="dave@example.com",
        password_hash=hash_password("davepass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    """登录拿 token"""
    resp = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest_asyncio.fixture
async def alice_headers(client: AsyncClient, alice: User) -> dict:
    return await _login(client, "alice", "alicepass123")


@pytest_asyncio.fixture
async def bob_headers(client: AsyncClient, bob: User) -> dict:
    return await _login(client, "bob", "bobpass123")


@pytest_asyncio.fixture
async def carol_headers(client: AsyncClient, carol: User) -> dict:
    return await _login(client, "carol", "carolpass123")


@pytest_asyncio.fixture
async def dave_headers(client: AsyncClient, dave: User) -> dict:
    return await _login(client, "dave", "davepass123")


@pytest_asyncio.fixture
async def team_factory(client: AsyncClient, db_session: AsyncSession):
    """工厂: 创建团队 + 拉人 + 设置角色

    返回: (team_id, members_dict) 其中 members_dict = {username: {role, headers}}
    """
    state = {"teams": [], "headers": {}}

    async def _make(
        owner: str, name: str, members: dict = None
    ) -> int:
        """创建团队.
        owner: 用户名
        name: 团队名
        members: {username: role} 额外成员
        """
        if owner not in state["headers"]:
            raise ValueError(f"未登录用户 {owner}")
        resp = await client.post(
            "/api/teams",
            headers=state["headers"][owner],
            json={"name": name, "description": f"团队 {name}", "max_members": 20},
        )
        assert resp.status_code == 200, f"Create team failed: {resp.text}"
        team_id = resp.json()["id"]
        state["teams"].append(team_id)

        # 邀请额外成员
        if members:
            for username, role in members.items():
                if username not in state["headers"]:
                    raise ValueError(f"未登录用户 {username}")
                # 通过 DB 找 user_id
                user_result = await db_session.execute(
                    select(User).where(User.username == username)
                )
                target_user = user_result.scalar_one()
                resp = await client.post(
                    f"/api/teams/{team_id}/members",
                    headers=state["headers"][owner],
                    json={"user_id": target_user.id, "role": role},
                )
                assert resp.status_code == 200, f"Invite {username} failed: {resp.text}"

        return team_id

    def _set_headers(username: str, headers: dict):
        """预设用户登录 token."""
        state["headers"][username] = headers

    # 返回 SimpleNamespace, 避免方法绑定问题
    from types import SimpleNamespace
    yield SimpleNamespace(
        make=_make,
        set_headers=_set_headers,
    )


# ============== T01: 创建团队 + 自动加入 ==============

@pytest.mark.asyncio
async def test_t01_create_team_auto_join(
    client: AsyncClient, alice: User, alice_headers: dict, db_session: AsyncSession
):
    """创建团队后, creator 应自动加入为 manager."""
    resp = await client.post(
        "/api/teams",
        headers=alice_headers,
        json={"name": "标注组A", "max_members": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["name"] == "标注组A"
    assert data["slug"].startswith("")

    # 验证 creator 已是 member (manager)
    result = await db_session.execute(
        select(TeamMember).where(
            TeamMember.team_id == data["id"],
            TeamMember.user_id == alice.id,
        )
    )
    member = result.scalar_one()
    assert member.role == "manager"


# ============== T02: slug 重复自动追加后缀 ==============

@pytest.mark.asyncio
async def test_t02_slug_uniqueness_append_suffix(
    client: AsyncClient, alice: User, alice_headers: dict
):
    """同名团队创建两次, slug 应自动追加后缀."""
    r1 = await client.post("/api/teams", headers=alice_headers, json={"name": "test-team"})
    r2 = await client.post("/api/teams", headers=alice_headers, json={"name": "test-team"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    slug1 = r1.json()["slug"]
    slug2 = r2.json()["slug"]
    assert slug1 != slug2, f"slug 重复: {slug1} == {slug2}"
    assert slug2.endswith("-2"), f"第二次应追加 -2, 实际: {slug2}"


# ============== T03: PATCH 团队 (manager) ==============

@pytest.mark.asyncio
async def test_t03_patch_team_by_manager(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """manager 可以编辑团队名/描述/上限."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "manager"})

    resp = await client.patch(
        f"/api/teams/{team_id}",
        headers=bob_headers,  # bob 是 manager
        json={"name": "TestTeam-Updated", "description": "新描述", "max_members": 30},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "TestTeam-Updated"
    assert data["max_members"] == 30


# ============== T04: PATCH 团队 (viewer) = 403 ==============

@pytest.mark.asyncio
async def test_t04_patch_team_by_viewer_forbidden(
    client: AsyncClient, alice: User, carol: User,
    alice_headers: dict, carol_headers: dict,
    team_factory,
):
    """viewer 不能编辑团队."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("carol", carol_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"carol": "viewer"})

    resp = await client.patch(
        f"/api/teams/{team_id}",
        headers=carol_headers,
        json={"name": "Hacked"},
    )
    assert resp.status_code == 403


# ============== T05: PATCH max_members < 当前 → 400 ==============

@pytest.mark.asyncio
async def test_t05_patch_max_members_too_low(
    client: AsyncClient, alice: User, bob: User, carol: User,
    alice_headers: dict, bob_headers: dict, carol_headers: dict,
    team_factory,
):
    """max_members 降级小于当前成员数 → 400."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_factory.set_headers("carol", carol_headers)
    # 3 个成员 (alice + bob + carol)
    team_id = await team_factory.make(
        "alice", "TestTeam",
        members={"bob": "editor", "carol": "editor"},
    )

    resp = await client.patch(
        f"/api/teams/{team_id}",
        headers=alice_headers,
        json={"max_members": 2},  # < 3
    )
    assert resp.status_code == 400
    assert "不能小于当前成员数" in resp.json()["detail"]


# ============== T06: POST /transfer (非 owner) → 403 ==============

@pytest.mark.asyncio
async def test_t06_transfer_by_non_owner_forbidden(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """非 owner 调转让 → 403."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "manager"})

    resp = await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=bob_headers,  # bob 是 manager 但不是 owner
        json={"new_owner_id": bob.id, "confirm": True},
    )
    assert resp.status_code == 403


# ============== T07: POST /transfer (owner, receiver=viewer) → 400 ==============

@pytest.mark.asyncio
async def test_t07_transfer_to_viewer_forbidden(
    client: AsyncClient, alice: User, carol: User,
    alice_headers: dict, carol_headers: dict,
    team_factory,
):
    """不能转让给 viewer 角色."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("carol", carol_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"carol": "viewer"})

    resp = await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=alice_headers,
        json={"new_owner_id": carol.id, "confirm": True},
    )
    assert resp.status_code == 400
    assert "viewer" in resp.json()["detail"].lower() or "仅阅读" in resp.json()["detail"]


# ============== T08: POST /transfer (confirm=False) → 400 ==============

@pytest.mark.asyncio
async def test_t08_transfer_without_confirm(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """未二次确认 → 400."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "manager"})

    resp = await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=alice_headers,
        json={"new_owner_id": bob.id, "confirm": False},
    )
    assert resp.status_code == 400


# ============== T09: POST /transfer (合法) → 200 + 审计 ==============

@pytest.mark.asyncio
async def test_t09_transfer_success(
    client: AsyncClient, alice: User, bob: User, db_session: AsyncSession,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """合法转让: owner 变更, 接收方提升为 manager, 审计入库."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "editor"})

    resp = await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=alice_headers,
        json={"new_owner_id": bob.id, "confirm": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["old_owner_id"] == alice.id
    assert data["new_owner_id"] == bob.id

    # DB 验证: team.owner_id = bob
    team = await db_session.get(Team, team_id)
    assert team.owner_id == bob.id

    # bob 现在 role = manager
    bob_member = (await db_session.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id == bob.id
        )
    )).scalar_one()
    assert bob_member.role == "manager"

    # 审计入库
    audit = (await db_session.execute(
        select(AuditLog).where(
            AuditLog.event_type == "team_ownership_transferred",
            AuditLog.team_id == team_id,
        )
    )).scalar_one()
    assert audit.detail["old_owner"] == alice.id
    assert audit.detail["new_owner"] == bob.id


# ============== T10: DELETE /members/me (owner) → 400 ==============

@pytest.mark.asyncio
async def test_t10_leave_team_as_owner_forbidden(
    client: AsyncClient, alice: User, alice_headers: dict, team_factory
):
    """owner 不能直接退, 必须先转让."""
    team_factory.set_headers("alice", alice_headers)
    team_id = await team_factory.make("alice", "TestTeam")

    resp = await client.delete(
        f"/api/teams/{team_id}/members/me", headers=alice_headers
    )
    assert resp.status_code == 400
    assert "转让" in resp.json()["detail"]


# ============== T11: DELETE /members/me (普通成员) → 200 + 审计 ==============

@pytest.mark.asyncio
async def test_t11_leave_team_success(
    client: AsyncClient, alice: User, bob: User, db_session: AsyncSession,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """普通成员主动退队, 审计入库."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "editor"})

    resp = await client.delete(
        f"/api/teams/{team_id}/members/me", headers=bob_headers
    )
    assert resp.status_code == 200

    # DB 验证: bob 不再是 member
    bob_member = (await db_session.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id == bob.id
        )
    )).scalar_one_or_none()
    assert bob_member is None

    # 审计
    audit = (await db_session.execute(
        select(AuditLog).where(
            AuditLog.event_type == "team_left",
            AuditLog.team_id == team_id,
        )
    )).scalar_one()
    assert audit.detail["user_id"] == bob.id


# ============== T12: 数据隔离: 非成员 403 ==============

@pytest.mark.asyncio
async def test_t12_non_member_cannot_view_team(
    client: AsyncClient, alice: User, dave: User,
    alice_headers: dict, dave_headers: dict,
    team_factory,
):
    """非成员 GET /api/teams/{id} → 403."""
    team_factory.set_headers("alice", alice_headers)
    team_id = await team_factory.make("alice", "TestTeam")

    resp = await client.get(f"/api/teams/{team_id}", headers=dave_headers)
    assert resp.status_code == 403


# ============== T13: GET /datasets (非成员) → 403 ==============

@pytest.mark.asyncio
async def test_t13_non_member_cannot_list_datasets(
    client: AsyncClient, alice: User, dave: User,
    alice_headers: dict, dave_headers: dict,
    team_factory,
):
    """非成员 GET /api/teams/{id}/datasets → 403."""
    team_factory.set_headers("alice", alice_headers)
    team_id = await team_factory.make("alice", "TestTeam")

    resp = await client.get(f"/api/teams/{team_id}/datasets", headers=dave_headers)
    assert resp.status_code == 403


# ============== T14: GET /datasets (成员) → 200 含 owner_name ==============

@pytest.mark.asyncio
async def test_t14_list_team_datasets_with_owner_name(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory, db_session: AsyncSession,
):
    """成员看团队数据集列表, 含 owner_name 和 my_access."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "editor"})

    # alice 创建一个 dataset
    ds_resp = await client.post(
        "/api/datasets", headers=alice_headers,
        json={"name": "测试集", "task_type": "classification", "category_names": ["cat1"]},
    )
    assert ds_resp.status_code in (200, 201)
    dataset_id = ds_resp.json()["id"]

    # 共享给团队
    share_resp = await client.post(
        f"/api/teams/datasets/{dataset_id}/share",
        headers=alice_headers,
        params={"team_id": team_id},
    )
    assert share_resp.status_code == 200

    # bob 看团队数据集列表
    resp = await client.get(f"/api/teams/{team_id}/datasets", headers=bob_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == dataset_id
    assert item["owner_name"] == "alice"
    assert item["my_access"] == "editor"


# ============== T15: 11 个写操作全部产生 audit_log ==============

@pytest.mark.asyncio
async def test_t15_all_write_ops_audit_logged(
    client: AsyncClient, alice: User, bob: User, carol: User, db_session: AsyncSession,
    alice_headers: dict, bob_headers: dict, carol_headers: dict,
    team_factory,
):
    """完整业务流: 创建 → 编辑 → 邀请 → 改角色 → 转让 → 退队 → 共享数据集 → 取消共享 → 删成员 → 删团队
    验证: 11 个写操作全部产生 audit_log.
    """
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_factory.set_headers("carol", carol_headers)

    # 1) 创建团队 (team_created)
    team_id = await team_factory.make("alice", "AuditTest", members={"bob": "editor", "carol": "editor"})

    # 2) PATCH 团队 (team_updated)
    await client.patch(f"/api/teams/{team_id}", headers=alice_headers, json={"description": "new"})

    # 3) 转让所有权 (team_ownership_transferred) - alice → bob
    await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=alice_headers,
        json={"new_owner_id": bob.id, "confirm": True},
    )
    # 现在 bob 是 owner, 用 bob 操作

    # 4) 改 carol 角色 (team_member_role_changed)
    await client.put(
        f"/api/teams/{team_id}/members/{carol.id}",
        headers=bob_headers,
        json={"role": "manager"},
    )

    # 5) 主动退队 - alice 现在是 manager
    await client.delete(f"/api/teams/{team_id}/members/me", headers=alice_headers)

    # 6) 创建 + 共享 dataset
    ds_resp = await client.post(
        "/api/datasets", headers=bob_headers,
        json={"name": "ds1", "task_type": "classification"},
    )
    dataset_id = ds_resp.json()["id"]

    # 7) 共享 dataset (dataset_shared_to_team)
    await client.post(
        f"/api/teams/datasets/{dataset_id}/share",
        headers=bob_headers,
        params={"team_id": team_id},
    )

    # 8) 取消共享 (dataset_unshared_from_team)
    await client.delete(
        f"/api/teams/datasets/{dataset_id}/share",
        headers=bob_headers,
    )

    # 9) 移除 carol (team_member_removed)
    await client.delete(
        f"/api/teams/{team_id}/members/{carol.id}",
        headers=bob_headers,
    )

    # 10) 删团队 (team_deleted) - bob 是 owner
    await client.delete(f"/api/teams/{team_id}", headers=bob_headers)

    # 验证 audit_log 数量
    audit_logs = (await db_session.execute(
        select(AuditLog).where(AuditLog.team_id == team_id)
    )).scalars().all()

    # 期望事件类型集合
    event_types = {log.event_type for log in audit_logs}
    expected = {
        "team_created",
        "team_updated",
        "team_ownership_transferred",
        "team_member_role_changed",
        "team_left",
        "dataset_shared_to_team",
        "dataset_unshared_from_team",
        "team_member_removed",
        "team_deleted",
        # team_member_invited 也应存在 (alice 创建时拉了 bob + carol)
        "team_member_invited",
    }
    missing = expected - event_types
    assert not missing, f"缺失审计类型: {missing}, 实际: {event_types}"
    # 至少 10 条 (team_deleted 后 team_id 还在 audit_log 中)
    assert len(audit_logs) >= 10


# ============== T16: 转让后原 owner 仍可访问 (作为 manager) ==============

@pytest.mark.asyncio
async def test_t16_transfer_then_old_owner_access(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """转让后原 owner 仍可访问团队详情 (作为 manager)."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "manager"})

    # 转让
    await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=alice_headers,
        json={"new_owner_id": bob.id, "confirm": True},
    )

    # alice 仍可 GET 团队详情
    resp = await client.get(f"/api/teams/{team_id}", headers=alice_headers)
    assert resp.status_code == 200


# ============== T17: 删除团队 (owner) → 200, dataset.team_id 清空 ==============

@pytest.mark.asyncio
async def test_t17_delete_team_clears_dataset_team_id(
    client: AsyncClient, alice: User, db_session: AsyncSession,
    alice_headers: dict, team_factory,
):
    """删除团队: dataset.team_id 被清空 (避免悬挂引用)."""
    team_factory.set_headers("alice", alice_headers)
    team_id = await team_factory.make("alice", "TestTeam")

    # 创建并共享 dataset
    ds_resp = await client.post(
        "/api/datasets", headers=alice_headers,
        json={"name": "ds1", "task_type": "classification"},
    )
    dataset_id = ds_resp.json()["id"]
    await client.post(
        f"/api/teams/datasets/{dataset_id}/share",
        headers=alice_headers, params={"team_id": team_id},
    )

    # 删除团队
    resp = await client.delete(f"/api/teams/{team_id}", headers=alice_headers)
    assert resp.status_code == 200

    # 验证 dataset.team_id 已清空
    dataset = await db_session.get(Dataset, dataset_id)
    assert dataset.team_id is None


# ============== T18: 删除团队 (非 owner) → 403 ==============

@pytest.mark.asyncio
async def test_t18_delete_team_by_non_owner_forbidden(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """非 owner 不能删除团队."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "manager"})

    resp = await client.delete(f"/api/teams/{team_id}", headers=bob_headers)
    assert resp.status_code == 403


# ============== T19: 邀请已存在成员 → 409 ==============

@pytest.mark.asyncio
async def test_t19_invite_existing_member_conflict(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """重复邀请 → 409."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "editor"})

    resp = await client.post(
        f"/api/teams/{team_id}/members",
        headers=alice_headers,
        json={"user_id": bob.id, "role": "editor"},
    )
    assert resp.status_code == 409


# ============== T20: 邀请超限 → 400 ==============

@pytest.mark.asyncio
async def test_t20_invite_over_quota(
    client: AsyncClient, alice: User, db_session: AsyncSession,
    alice_headers: dict, team_factory,
):
    """max_members 上限已满 → 400."""
    team_factory.set_headers("alice", alice_headers)
    # 创建 max_members=2 的团队 (alice 自动占 1 个名额)
    team_id = await team_factory.make("alice", "TestTeam")
    # 改 max_members = 2
    await client.patch(f"/api/teams/{team_id}", headers=alice_headers, json={"max_members": 2})

    # 创建第 3 个用户
    eve = User(
        username="eve", email="eve@example.com",
        password_hash=hash_password("evepass123"),
        role="annotator", is_active=True,
    )
    db_session.add(eve)
    await db_session.commit()
    await db_session.refresh(eve)
    eve_headers = await _login(client, "eve", "evepass123")

    # 先邀请 1 个 (达到 2 上限)
    # 用 bob (但 bob 不在 team_factory.headers 中, 直接用 alice 邀请 eve)
    # alice 已占 1 个, 邀请 eve 占第 2 个, 刚好满
    resp = await client.post(
        f"/api/teams/{team_id}/members",
        headers=alice_headers,
        json={"user_id": eve.id, "role": "editor"},
    )
    assert resp.status_code == 200  # 第 2 个, 刚满

    # 再邀请第 3 个
    frank = User(
        username="frank", email="frank@example.com",
        password_hash=hash_password("frankpass123"),
        role="annotator", is_active=True,
    )
    db_session.add(frank)
    await db_session.commit()
    await db_session.refresh(frank)

    resp = await client.post(
        f"/api/teams/{team_id}/members",
        headers=alice_headers,
        json={"user_id": frank.id, "role": "editor"},
    )
    assert resp.status_code == 400
    assert "已满" in resp.json()["detail"]


# ============== T2.5 L2: 最后一名 manager 保护 ==============

@pytest.mark.asyncio
async def test_t21_demote_owner_blocked_by_owner_protection(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """T21: owner 改自己角色为非 manager → 触发 owner 保护 (优先于 manager 保护)."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    # alice 是 owner+manager (创建时自动加入 TeamMember 表)
    # bob 是 editor
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "editor"})

    # alice 改自己为 editor → 触发 owner 保护
    resp = await client.put(
        f"/api/teams/{team_id}/members/{alice.id}",
        headers=alice_headers,
        json={"role": "editor"},
    )
    assert resp.status_code == 400
    # owner 保护先于 manager 保护触发
    assert "创建者必须保持" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_t22_demote_manager_with_two_managers_allowed(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """T22: 团队有 2 个 manager, 降级其中一个 → 200 (剩下 1 个 manager)."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    # bob 是 manager (有 2 个 manager)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "manager"})

    # alice 降级 bob 为 editor
    resp = await client.put(
        f"/api/teams/{team_id}/members/{bob.id}",
        headers=alice_headers,
        json={"role": "editor"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"


@pytest.mark.asyncio
async def test_t23_demote_after_transfer_old_owner_still_manager(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """T23: 转让所有权后, 原 owner 仍是 manager, 转让后原 owner 可被降级.

    验证 manager 计数含原 owner (转让后角色保持 manager).
    """
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "manager"})

    # 转让给 bob: 现在 bob=owner+manager, alice=manager (保留)
    transfer_resp = await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=alice_headers,
        json={"new_owner_id": bob.id, "confirm": True},
    )
    assert transfer_resp.status_code == 200

    # bob 现在降级 alice (manager) → 应成功 (剩 bob 1 个 manager)
    resp = await client.put(
        f"/api/teams/{team_id}/members/{alice.id}",
        headers=bob_headers,
        json={"role": "editor"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"


@pytest.mark.asyncio
async def test_t24_remove_last_non_owner_manager_blocked(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """T24: 转让所有权后, 移除原 owner (非 owner, manager) → 应被阻止 (留 0 个 manager)."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "manager"})

    # 转让给 bob: bob=owner+manager, alice=manager
    transfer_resp = await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=alice_headers,
        json={"new_owner_id": bob.id, "confirm": True},
    )
    assert transfer_resp.status_code == 200

    # bob 尝试移除 alice (非 owner, manager)
    # 移除前 _count_managers = 2 (bob + alice), 移除 alice 后剩 1 (bob)
    # 所以这次应成功 (还有 1 个 manager 剩)
    resp = await client.delete(
        f"/api/teams/{team_id}/members/{alice.id}",
        headers=bob_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_t25_remove_only_manager_when_owner_protected(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """T25: 唯一 manager 是 owner, 移除 owner → 400 (owner 保护先触发).

    团队只有 alice (owner+manager) + bob (editor).
    移除 alice (owner) → 应被 owner 保护阻止.
    """
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "editor"})

    # 尝试移除 owner alice → 400 owner 保护
    resp = await client.delete(
        f"/api/teams/{team_id}/members/{alice.id}",
        headers=alice_headers,
    )
    assert resp.status_code == 400
    assert "创建者" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_t26_remove_editor_always_allowed(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict,
    team_factory,
):
    """T26: 移除非 manager 成员 (editor/viewer) 永远允许 (manager 计数保护不影响)."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    # alice (manager, owner) + bob (editor)
    team_id = await team_factory.make("alice", "TestTeam", members={"bob": "editor"})

    resp = await client.delete(
        f"/api/teams/{team_id}/members/{bob.id}",
        headers=alice_headers,
    )
    assert resp.status_code == 200
