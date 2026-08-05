"""
v3.3.2 协作增强 - 团队共享数据集集成测试
==========================================

覆盖用户 5 大新需求 (5 个功能模块):
  ① 团队管理界面「共享数据集」按钮 (shareable-datasets 端点)
  ② 团队共享数据集可见性 (list_datasets 返回 source=team_shared)
  ③ 数据集管理列表「数据来源」列 (source/team_name 字段)
  ④ 角色权限体系 (manager 才能共享; 仅 owner 能取消)
  ⑤ 共享数据集信息展示 (my_access_label 中文)

测试矩阵 (T21-T35):
  T21: 团队成员可见团队共享数据集 (新 source 字段)
  T22: 团队成员个人数据集仍正常显示
  T23: 共享给团队后, 团队所有成员在 /api/datasets 看到该 dataset
  T24: GET /teams/{id}/shareable-datasets: manager 可看到自己未共享的
  T25: GET /teams/{id}/shareable-datasets: editor 看到 → 403
  T26: GET /teams/{id}/shareable-datasets: viewer 看到 → 403
  T27: GET /teams/{id}/shareable-datasets: 非成员 → 403
  T28: editor 角色共享 dataset 到团队 → 403 (manager 限制)
  T29: manager 共享 dataset 到团队 → 200
  T30: editor 取消共享 dataset → 403 (仅 owner 可取消)
  T31: owner 取消共享 → 200
  T32: 共享 dataset 替换到别的团队 (二次保护: 旧团队自动解除)
  T33: my_access_label 中文 (manager → 可管理)
  T34: admin 取消共享 → 200 (admin 绕过)
  T35: admin 看到所有 dataset 含 source 字段
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
from app.middleware.security.security import hash_password


# ============== 测试用户 ==============

@pytest_asyncio.fixture
async def alice(db_session: AsyncSession) -> User:
    user = User(
        username="alice", email="alice@example.com",
        password_hash=hash_password("alicepass123"),
        role="annotator", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession) -> User:
    user = User(
        username="bob", email="bob@example.com",
        password_hash=hash_password("bobpass123"),
        role="annotator", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def carol(db_session: AsyncSession) -> User:
    user = User(
        username="carol", email="carol@example.com",
        password_hash=hash_password("carolpass123"),
        role="annotator", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin332", email="admin332@example.com",
        password_hash=hash_password("adminpass123"),
        role="admin", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
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
async def admin_headers(client: AsyncClient, admin_user: User) -> dict:
    return await _login(client, "admin332", "adminpass123")


@pytest_asyncio.fixture
async def team_factory(client: AsyncClient, db_session: AsyncSession):
    state = {"headers": {}}
    async def _make(
        owner: str, name: str, members: dict = None
    ) -> int:
        resp = await client.post(
            "/api/teams", headers=state["headers"][owner],
            json={"name": name, "max_members": 20},
        )
        assert resp.status_code == 200, resp.text
        team_id = resp.json()["id"]
        if members:
            for username, role in members.items():
                user_result = await db_session.execute(
                    select(User).where(User.username == username)
                )
                target = user_result.scalar_one()
                resp = await client.post(
                    f"/api/teams/{team_id}/members",
                    headers=state["headers"][owner],
                    json={"user_id": target.id, "role": role},
                )
                assert resp.status_code == 200, f"Invite {username} failed: {resp.text}"
        return team_id
    def _set_headers(username: str, headers: dict):
        state["headers"][username] = headers
    from types import SimpleNamespace
    yield SimpleNamespace(make=_make, set_headers=_set_headers)


async def _create_dataset(
    client: AsyncClient, headers: dict, name: str
) -> int:
    resp = await client.post(
        "/api/datasets", headers=headers,
        json={"name": name, "task_type": "classification"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


# ============== T21: 团队共享数据集可见性 ==============

@pytest.mark.asyncio
async def test_t21_team_member_sees_team_shared_dataset(
    client: AsyncClient, alice: User, bob: User, carol: User,
    alice_headers: dict, bob_headers: dict, carol_headers: dict,
    team_factory,
):
    """共享给团队后, 团队所有成员 (manager/editor/viewer) 均能在 /api/datasets 中看到该 dataset."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_factory.set_headers("carol", carol_headers)
    team_id = await team_factory.make(
        "alice", "T21Team",
        members={"bob": "editor", "carol": "viewer"},
    )
    # alice 创建并共享 dataset
    ds_id = await _create_dataset(client, alice_headers, "alice-shared-ds")
    r = await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers, params={"team_id": team_id},
    )
    assert r.status_code == 200, r.text

    # bob (editor) 在 /api/datasets 中应能看到
    r = await client.get("/api/datasets", headers=bob_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    ids = [it["id"] for it in items]
    assert ds_id in ids, f"bob 没看到共享 dataset, items={ids}"
    # 找该条目
    shared_item = next(it for it in items if it["id"] == ds_id)
    assert shared_item["source"] == "team_shared"
    assert shared_item["team_id"] == team_id
    assert shared_item["team_name"] == "T21Team"
    assert shared_item["shared_by"] == "alice"
    assert shared_item["my_access"] == "editor"

    # carol (viewer) 也能看到
    r = await client.get("/api/datasets", headers=carol_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    ids = [it["id"] for it in items]
    assert ds_id in ids
    shared_item = next(it for it in items if it["id"] == ds_id)
    assert shared_item["my_access"] == "viewer"


# ============== T22: 个人数据集仍正常显示 ==============

@pytest.mark.asyncio
async def test_t22_personal_dataset_display(
    client: AsyncClient, alice: User,
    alice_headers: dict,
):
    """未共享的 dataset 在 /api/datasets 中 source=personal, 无 team_id/team_name."""
    ds_id = await _create_dataset(client, alice_headers, "alice-personal-ds")
    r = await client.get("/api/datasets", headers=alice_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    item = next(it for it in items if it["id"] == ds_id)
    assert item["source"] == "personal"
    assert item["team_id"] is None
    assert item["team_name"] is None
    assert item["my_access"] == "owner"


# ============== T23: 退出团队后, 共享数据集不再可见 ==============

@pytest.mark.asyncio
async def test_t23_leave_team_hides_shared_dataset(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """bob 退出团队后, 之前共享给团队的 dataset 不再出现在 bob 的列表中."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make("alice", "T23Team", members={"bob": "editor"})
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-23")
    await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers, params={"team_id": team_id},
    )

    # bob 退队
    r = await client.delete(f"/api/teams/{team_id}/members/me", headers=bob_headers)
    assert r.status_code == 200

    # bob 列表中不再有
    r = await client.get("/api/datasets", headers=bob_headers)
    items = r.json()["items"]
    ids = [it["id"] for it in items]
    assert ds_id not in ids, "bob 退队后仍能看到共享 dataset"


# ============== T24: shareable-datasets 端点: manager 可看到 ==============

@pytest.mark.asyncio
async def test_t24_shareable_datasets_manager_sees(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """manager 角色调用 /shareable-datasets 可看到自己未共享的 dataset."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make(
        "alice", "T24Team", members={"bob": "manager"},
    )
    # bob 创建 2 个 dataset, 都不共享
    ds1 = await _create_dataset(client, bob_headers, "bob-ds-1")
    ds2 = await _create_dataset(client, bob_headers, "bob-ds-2")

    r = await client.get(
        f"/api/teams/{team_id}/shareable-datasets", headers=bob_headers
    )
    assert r.status_code == 200
    data = r.json()
    ids = [it["id"] for it in data["items"]]
    assert ds1 in ids
    assert ds2 in ids
    assert data["total"] >= 2


# ============== T25: shareable-datasets: editor → 403 ==============

@pytest.mark.asyncio
async def test_t25_shareable_datasets_editor_forbidden(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """editor 角色调用 /shareable-datasets → 403 (需 manager)."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make(
        "alice", "T25Team", members={"bob": "editor"},
    )
    r = await client.get(
        f"/api/teams/{team_id}/shareable-datasets", headers=bob_headers
    )
    assert r.status_code == 403


# ============== T26: shareable-datasets: viewer → 403 ==============

@pytest.mark.asyncio
async def test_t26_shareable_datasets_viewer_forbidden(
    client: AsyncClient, alice: User, carol: User,
    alice_headers: dict, carol_headers: dict, team_factory,
):
    """viewer 角色调用 /shareable-datasets → 403."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("carol", carol_headers)
    team_id = await team_factory.make(
        "alice", "T26Team", members={"carol": "viewer"},
    )
    r = await client.get(
        f"/api/teams/{team_id}/shareable-datasets", headers=carol_headers
    )
    assert r.status_code == 403


# ============== T27: shareable-datasets: 非成员 → 403 ==============

@pytest.mark.asyncio
async def test_t27_shareable_datasets_non_member_forbidden(
    client: AsyncClient, alice: User, carol: User,
    alice_headers: dict, carol_headers: dict, team_factory,
):
    """非成员调用 /shareable-datasets → 403 (数据隔离)."""
    team_factory.set_headers("alice", alice_headers)
    team_id = await team_factory.make("alice", "T27Team")
    r = await client.get(
        f"/api/teams/{team_id}/shareable-datasets", headers=carol_headers
    )
    assert r.status_code == 403


# ============== T28: editor 共享 dataset → 403 ==============

@pytest.mark.asyncio
async def test_t28_share_dataset_editor_forbidden(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """editor 角色共享自己拥有的 dataset 到团队 → 403 (需 manager)."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make(
        "alice", "T28Team", members={"bob": "editor"},
    )
    # bob 创建 dataset (他是 owner)
    ds_id = await _create_dataset(client, bob_headers, "bob-ds-28")

    # bob 尝试共享 → 应被拒
    r = await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=bob_headers, params={"team_id": team_id},
    )
    assert r.status_code == 403
    assert "可管理" in r.json()["detail"]


# ============== T29: manager 共享 dataset → 200 ==============

@pytest.mark.asyncio
async def test_t29_share_dataset_manager_ok(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """manager 角色将自己拥有的 dataset 共享到团队 → 200."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make(
        "alice", "T29Team", members={"bob": "manager"},
    )
    ds_id = await _create_dataset(client, bob_headers, "bob-ds-29")
    r = await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=bob_headers, params={"team_id": team_id},
    )
    assert r.status_code == 200
    assert r.json()["team_id"] == team_id


# ============== T30: editor 取消共享 → 403 ==============

@pytest.mark.asyncio
async def test_t30_unshare_dataset_editor_forbidden(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """editor 角色取消别人共享的 dataset → 403 (仅 owner 可取消)."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make(
        "alice", "T30Team", members={"bob": "editor"},
    )
    # alice 创建并共享
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-30")
    r = await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers, params={"team_id": team_id},
    )
    assert r.status_code == 200

    # bob (editor, 非 owner) 尝试取消 → 403
    r = await client.delete(
        f"/api/teams/datasets/{ds_id}/share", headers=bob_headers
    )
    assert r.status_code == 403
    assert "原始共享者" in r.json()["detail"]


# ============== T31: owner 取消共享 → 200 ==============

@pytest.mark.asyncio
async def test_t31_unshare_dataset_owner_ok(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """owner 取消共享 → 200, dataset.team_id 清空."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make(
        "alice", "T31Team", members={"bob": "editor"},
    )
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-31")
    await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers, params={"team_id": team_id},
    )

    r = await client.delete(
        f"/api/teams/datasets/{ds_id}/share", headers=alice_headers
    )
    assert r.status_code == 200
    assert r.json()["team_id"] is None


# ============== T32: 共享替换 (dataset 已在别的团队, 换到新团队) ==============

@pytest.mark.asyncio
async def test_t32_share_replace_auto_unshare_old(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """dataset 已共享给团队 A, 再共享给团队 B, 旧团队 A 自动解除."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_a = await team_factory.make("alice", "T32TeamA", members={"bob": "manager"})
    team_b = await team_factory.make("alice", "T32TeamB", members={"bob": "manager"})
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-32")

    # 先共享到 A
    r = await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers, params={"team_id": team_a},
    )
    assert r.status_code == 200

    # 再共享到 B (应自动解除 A)
    r = await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers, params={"team_id": team_b},
    )
    assert r.status_code == 200
    assert r.json()["team_id"] == team_b

    # 验证: A 的成员 bob 看不到该 dataset
    r = await client.get("/api/datasets", headers=bob_headers)
    item_b = next(
        (it for it in r.json()["items"] if it["id"] == ds_id), None
    )
    # bob 同时在 A 和 B, 但 dataset 现在在 B
    if item_b is not None:
        assert item_b["team_id"] == team_b


# ============== T33: my_access_label 中文 ==============

@pytest.mark.asyncio
async def test_t33_my_access_label_chinese(
    client: AsyncClient, alice: User, bob: User, carol: User,
    alice_headers: dict, bob_headers: dict, carol_headers: dict,
    team_factory,
):
    """团队数据集列表中 my_access_label 返回中文."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_factory.set_headers("carol", carol_headers)
    team_id = await team_factory.make(
        "alice", "T33Team",
        members={"bob": "editor", "carol": "viewer"},
    )
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-33")
    await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers, params={"team_id": team_id},
    )

    # bob 看到: my_access_label = "可编辑"
    r = await client.get(f"/api/teams/{team_id}/datasets", headers=bob_headers)
    item = r.json()["items"][0]
    assert item["my_access_label"] == "可编辑"

    # carol 看到: my_access_label = "可阅读"
    r = await client.get(f"/api/teams/{team_id}/datasets", headers=carol_headers)
    item = r.json()["items"][0]
    assert item["my_access_label"] == "可阅读"

    # alice (owner/manager) 看到: my_access_label = "可管理"
    r = await client.get(f"/api/teams/{team_id}/datasets", headers=alice_headers)
    item = r.json()["items"][0]
    assert item["my_access_label"] == "可管理"


# ============== T34: admin 取消共享 → 200 ==============

@pytest.mark.asyncio
async def test_t34_unshare_dataset_admin_ok(
    client: AsyncClient, alice: User, admin_user: User,
    alice_headers: dict, admin_headers: dict, team_factory,
):
    """admin 可绕过 owner 限制, 直接取消共享."""
    team_factory.set_headers("alice", alice_headers)
    team_id = await team_factory.make("alice", "T34Team")
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-34")
    await client.post(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers, params={"team_id": team_id},
    )

    # admin 取消
    r = await client.delete(
        f"/api/teams/datasets/{ds_id}/share", headers=admin_headers
    )
    assert r.status_code == 200


# ============== T35: admin 看所有 dataset 含 source 字段 ==============

@pytest.mark.asyncio
async def test_t35_admin_sees_all_with_source(
    client: AsyncClient, alice: User, admin_user: User,
    alice_headers: dict, admin_headers: dict, team_factory,
):
    """admin 看 /api/datasets 应看到所有 dataset, 含 source 字段 (personal 或 team_shared)."""
    team_factory.set_headers("alice", alice_headers)
    team_id = await team_factory.make("alice", "T35Team")
    ds_personal = await _create_dataset(client, alice_headers, "alice-personal-35")
    ds_shared = await _create_dataset(client, alice_headers, "alice-shared-35")
    await client.post(
        f"/api/teams/datasets/{ds_shared}/share",
        headers=alice_headers, params={"team_id": team_id},
    )

    r = await client.get("/api/datasets", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    ids = {it["id"]: it for it in items}
    assert ds_personal in ids
    assert ds_shared in ids
    # 字段均存在
    for it in items:
        assert "source" in it
        assert "team_id" in it
        assert "team_name" in it
        assert "my_access" in it


# ============== T36: 数据集列表 total 字段 ==============

@pytest.mark.asyncio
async def test_t36_dataset_list_total_field(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """数据集列表响应顶层含 total / personal_count / team_shared_count 字段."""
    team_factory.set_headers("alice", alice_headers)
    team_factory.set_headers("bob", bob_headers)
    team_id = await team_factory.make(
        "alice", "T36Team", members={"bob": "manager"},
    )
    # bob 创建一个 personal dataset
    await _create_dataset(client, bob_headers, "bob-ds-36")

    r = await client.get("/api/datasets", headers=bob_headers)
    data = r.json()
    assert "total" in data
    assert "personal_count" in data
    assert "team_shared_count" in data
    assert data["personal_count"] >= 1
    assert data["total"] == len(data["items"])
