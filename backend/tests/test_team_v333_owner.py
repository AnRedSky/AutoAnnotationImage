"""
v3.3.3 协作权限精细化 - 集成测试
====================================

覆盖用户 4 项新需求 (4 个功能模块):
  ① 数据集管理功能优化 (owner 视角下 source=personal)
  ② 团队管理权限控制 (仅 owner 可取消共享)
  ③ 团队成员管理界面优化 (所有者标注「（所有者）」)
  ④ 团队动态记录规范 (detail_message 中文自然语言描述)

测试矩阵 (V01-V15):
  V01: owner 自己看自己 dataset (已分享给团队) → source=personal
  V02: 非 owner 视角下, 同一 dataset → source=team_shared
  V03: 列表中 personal_count/team_shared_count 按 owner 视角计算
  V04: admin 视角下, 别人的 dataset (已分享) 仍为 team_shared
  V05: editor 取消共享 → 403 (严格仅 owner)
  V06: viewer 取消共享 → 403
  V07: team owner 取消共享别人的 dataset → 403 (与 team 角色无关)
  V08: dataset owner 取消共享 → 200
  V09: list_members 返回 is_owner 字段 (owner=true/false)
  V10: 转让所有权后, 新 owner 的 is_owner=true, 旧 owner=false
  V11: activities 端点返回 detail_message 字段
  V12: detail_message 内容含「创建了团队」中文
  V13: detail_message 内容含「邀请了」「角色为「可编辑」」中文
  V14: detail_message 内容含「共享了」中文 (共享数据集)
  V15: detail_message 内容含「从「可编辑」调整为「可管理」」中文
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
        username="alice333", email="alice333@example.com",
        password_hash=hash_password("alicepass333"),
        role="annotator", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession) -> User:
    user = User(
        username="bob333", email="bob333@example.com",
        password_hash=hash_password("bobpass333"),
        role="annotator", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def carol(db_session: AsyncSession) -> User:
    user = User(
        username="carol333", email="carol333@example.com",
        password_hash=hash_password("carolpass333"),
        role="annotator", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def dave(db_session: AsyncSession) -> User:
    """dave 是另一个团队的 owner, 用于转让测试."""
    user = User(
        username="dave333", email="dave333@example.com",
        password_hash=hash_password("davepass333"),
        role="annotator", is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin333", email="admin333@example.com",
        password_hash=hash_password("adminpass333"),
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
    return await _login(client, "alice333", "alicepass333")


@pytest_asyncio.fixture
async def bob_headers(client: AsyncClient, bob: User) -> dict:
    return await _login(client, "bob333", "bobpass333")


@pytest_asyncio.fixture
async def carol_headers(client: AsyncClient, carol: User) -> dict:
    return await _login(client, "carol333", "carolpass333")


@pytest_asyncio.fixture
async def dave_headers(client: AsyncClient, dave: User) -> dict:
    return await _login(client, "dave333", "davepass333")


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, admin_user: User) -> dict:
    return await _login(client, "admin333", "adminpass333")


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


async def _share_dataset(
    client: AsyncClient, headers: dict, dataset_id: int, team_id: int
) -> None:
    resp = await client.post(
        f"/api/teams/datasets/{dataset_id}/share",
        headers=headers, params={"team_id": team_id},
    )
    assert resp.status_code == 200, resp.text


# ============== 需求 ①: owner 视角下 source=personal ==============

@pytest.mark.asyncio
async def test_v01_owner_sees_own_shared_dataset_as_personal(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """alice 把自己创建的 dataset 分享给团队, 在自己视角下应仍为「个人所有」.

    用户新需求 §1: 当当前登录用户对某数据集拥有「所有者」权限时,
    系统应自动将该数据集的数据来源属性设置为「个人所有」, 而非「团队共享」.
    """
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V01Team", members={"bob333": "editor"})

    ds_id = await _create_dataset(client, alice_headers, "alice-shared-v01")
    await _share_dataset(client, alice_headers, ds_id, team_id)

    # alice (owner) 视角: source=personal
    r = await client.get("/api/datasets", headers=alice_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    own_item = next(it for it in items if it["id"] == ds_id)
    assert own_item["source"] == "personal", \
        f"owner 视角下应为 personal, 实际={own_item['source']}"
    assert own_item["team_id"] is None
    assert own_item["team_name"] is None
    assert own_item["shared_by"] is None
    assert own_item["my_access"] == "owner"


@pytest.mark.asyncio
async def test_v02_non_owner_sees_shared_dataset_as_team_shared(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """非 owner (bob) 看 alice 分享的 dataset, 应为「团队共享」."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V02Team", members={"bob333": "editor"})

    ds_id = await _create_dataset(client, alice_headers, "alice-shared-v02")
    await _share_dataset(client, alice_headers, ds_id, team_id)

    # bob (非 owner) 视角: source=team_shared
    r = await client.get("/api/datasets", headers=bob_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    item = next(it for it in items if it["id"] == ds_id)
    assert item["source"] == "team_shared"
    assert item["team_id"] == team_id
    assert item["team_name"] == "V02Team"
    assert item["shared_by"] == "alice333"
    assert item["my_access"] == "editor"


@pytest.mark.asyncio
async def test_v03_counts_separated_by_owner_perspective(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """owner 视角下, 自己分享的算 personal; 别人视角下, 同 dataset 算 team_shared.

    用户新需求 §1: 个人计数 personal_count / team_shared_count 同样按「当前用户视角」计算.
    """
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V03Team", members={"bob333": "editor"})

    # alice 创建 2 个: 1 个共享给团队, 1 个不共享
    shared_ds = await _create_dataset(client, alice_headers, "alice-shared-v03")
    personal_ds = await _create_dataset(client, alice_headers, "alice-personal-v03")
    await _share_dataset(client, alice_headers, shared_ds, team_id)

    # alice 视角: 2 personal, 0 team_shared
    r = await client.get("/api/datasets", headers=alice_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["personal_count"] == 2
    assert data["team_shared_count"] == 0

    # bob 视角: 0 personal, 1 team_shared
    r = await client.get("/api/datasets", headers=bob_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["personal_count"] == 0
    assert data["team_shared_count"] == 1


@pytest.mark.asyncio
async def test_v04_admin_perspective_for_others_dataset(
    client: AsyncClient, alice: User, admin_user: User,
    alice_headers: dict, admin_headers: dict, team_factory,
):
    """admin 看 alice 分享给团队的 dataset, 应为「团队共享」(非 personal)."""
    team_factory.set_headers("alice333", alice_headers)
    team_id = await team_factory.make("alice333", "V04Team")
    ds_id = await _create_dataset(client, alice_headers, "alice-shared-v04")
    await _share_dataset(client, alice_headers, ds_id, team_id)

    # admin 视角
    r = await client.get("/api/datasets", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    item = next(it for it in items if it["id"] == ds_id)
    assert item["source"] == "team_shared"
    assert item["team_id"] == team_id
    assert item["my_access"] == "admin"


# ============== 需求 ②: 仅 owner 可取消共享 ==============

@pytest.mark.asyncio
async def test_v05_editor_cannot_unshare(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """editor 角色不能取消 owner 分享的 dataset."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V05Team", members={"bob333": "editor"})
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-v05")
    await _share_dataset(client, alice_headers, ds_id, team_id)

    # bob 尝试取消 → 403
    r = await client.delete(
        f"/api/teams/datasets/{ds_id}/share",
        headers=bob_headers,
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_v06_viewer_cannot_unshare(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """viewer 角色不能取消 owner 分享的 dataset."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V06Team", members={"bob333": "viewer"})
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-v06")
    await _share_dataset(client, alice_headers, ds_id, team_id)

    r = await client.delete(
        f"/api/teams/datasets/{ds_id}/share",
        headers=bob_headers,
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_v07_team_owner_cannot_unshare_others_dataset(
    client: AsyncClient, alice: User, carol: User, bob: User,
    alice_headers: dict, carol_headers: dict, bob_headers: dict, team_factory,
):
    """team 的 owner (alice) 不能取消 team member (carol) 分享到本团队的 dataset.

    与团队角色无关, 仅 dataset 的原始 owner 能取消.
    """
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("carol333", carol_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make(
        "alice333", "V07Team",
        members={"carol333": "manager", "bob333": "editor"},
    )

    # carol 创建并分享
    ds_id = await _create_dataset(client, carol_headers, "carol-ds-v07")
    await _share_dataset(client, carol_headers, ds_id, team_id)

    # alice (team owner) 尝试取消 carol 的 dataset → 403
    r = await client.delete(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers,
    )
    assert r.status_code == 403, \
        f"team owner 取消别人的 dataset 应被拒, 实际={r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_v08_dataset_owner_can_unshare(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """dataset 的 owner (alice) 能成功取消共享."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V08Team", members={"bob333": "editor"})
    ds_id = await _create_dataset(client, alice_headers, "alice-ds-v08")
    await _share_dataset(client, alice_headers, ds_id, team_id)

    r = await client.delete(
        f"/api/teams/datasets/{ds_id}/share",
        headers=alice_headers,
    )
    assert r.status_code == 200, r.text

    # 验证: bob 视角下该 dataset 已不可见
    r = await client.get("/api/datasets", headers=bob_headers)
    assert r.status_code == 200
    ids = [it["id"] for it in r.json()["items"]]
    assert ds_id not in ids


# ============== 需求 ③: list_members 返回 is_owner ==============

@pytest.mark.asyncio
async def test_v09_list_members_returns_is_owner(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """list_members 端点返回 is_owner 字段 (alice=team 创建者=True, bob=普通成员=False)."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V09Team", members={"bob333": "editor"})

    r = await client.get(f"/api/teams/{team_id}/members", headers=alice_headers)
    assert r.status_code == 200
    members = r.json()["items"]
    assert len(members) == 2

    alice_row = next(m for m in members if m["username"] == "alice333")
    bob_row = next(m for m in members if m["username"] == "bob333")
    assert alice_row["is_owner"] is True
    assert bob_row["is_owner"] is False


@pytest.mark.asyncio
async def test_v10_is_owner_updates_after_transfer(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """转让所有权后, is_owner 字段更新到新 owner."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V10Team", members={"bob333": "editor"})

    # 转让: alice → bob
    members_resp = await client.get(
        f"/api/teams/{team_id}/members", headers=alice_headers
    )
    members_data = members_resp.json()
    bob_user = members_data["items"]
    bob_id = next(m["user_id"] for m in bob_user if m["username"] == "bob333")

    r = await client.post(
        f"/api/teams/{team_id}/transfer",
        headers=alice_headers, json={"new_owner_id": bob_id, "confirm": True},
    )
    assert r.status_code == 200, r.text

    # 验证 is_owner 字段
    r = await client.get(f"/api/teams/{team_id}/members", headers=bob_headers)
    assert r.status_code == 200
    members = r.json()["items"]
    alice_row = next(m for m in members if m["username"] == "alice333")
    bob_row = next(m for m in members if m["username"] == "bob333")
    assert alice_row["is_owner"] is False
    assert bob_row["is_owner"] is True


# ============== 需求 ④: activities 返回中文 detail_message ==============

@pytest.mark.asyncio
async def test_v11_activities_response_has_detail_message(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """activities 端点响应 items 中每条都含 detail_message 字段."""
    team_factory.set_headers("alice333", alice_headers)
    team_id = await team_factory.make("alice333", "V11Team")

    r = await client.get(
        f"/api/teams/{team_id}/activities",
        headers=alice_headers,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    for item in items:
        assert "detail_message" in item, f"item 缺少 detail_message: {item}"
        assert item["detail_message"], f"detail_message 不应为空: {item}"


@pytest.mark.asyncio
async def test_v12_team_created_chinese_message(
    client: AsyncClient, alice: User,
    alice_headers: dict, team_factory,
):
    """team_created 事件的 detail_message 应含「创建了团队」中文."""
    team_factory.set_headers("alice333", alice_headers)
    team_id = await team_factory.make("alice333", "V12Team")

    r = await client.get(
        f"/api/teams/{team_id}/activities?event_type=team_created",
        headers=alice_headers,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    msg = items[0]["detail_message"]
    assert "alice333" in msg, f"消息应含触发人: {msg}"
    assert "创建了团队" in msg, f"消息应含「创建了团队」中文: {msg}"
    assert "V12Team" in msg, f"消息应含团队名: {msg}"
    # 验证不含英文 / 变量
    assert "{actor_name}" not in msg
    assert "{name}" not in msg


@pytest.mark.asyncio
async def test_v13_team_member_invited_chinese_message(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """team_member_invited 事件的 detail_message 含「邀请了」+ 角色中文."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V13Team", members={"bob333": "editor"})

    r = await client.get(
        f"/api/teams/{team_id}/activities?event_type=team_member_invited",
        headers=alice_headers,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    msg = items[0]["detail_message"]
    assert "alice333" in msg
    assert "邀请了" in msg
    assert "bob333" in msg
    assert "可编辑" in msg, f"角色应翻译为「可编辑」: {msg}"
    # 不应含英文 key
    assert "invitee_id" not in msg
    assert "role:" not in msg


@pytest.mark.asyncio
async def test_v14_dataset_shared_chinese_message(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """dataset_shared_to_team 事件的 detail_message 含「把数据集」+ 团队名中文."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V14Team", members={"bob333": "editor"})
    ds_id = await _create_dataset(client, alice_headers, "ds-v14-name")
    await _share_dataset(client, alice_headers, ds_id, team_id)

    r = await client.get(
        f"/api/teams/{team_id}/activities?event_type=dataset_shared_to_team",
        headers=alice_headers,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    msg = items[0]["detail_message"]
    assert "alice333" in msg
    assert "把数据集" in msg
    assert "ds-v14-name" in msg
    assert "V14Team" in msg
    # 不应含英文 key / JSON
    assert "dataset_id" not in msg
    assert "team_id" not in msg
    assert "{" not in msg, f"消息不应含 JSON 格式: {msg}"


@pytest.mark.asyncio
async def test_v15_role_change_chinese_message(
    client: AsyncClient, alice: User, bob: User,
    alice_headers: dict, bob_headers: dict, team_factory,
):
    """team_member_role_changed 事件的 detail_message 含「从「可编辑」调整为「可管理」」中文."""
    team_factory.set_headers("alice333", alice_headers)
    team_factory.set_headers("bob333", bob_headers)
    team_id = await team_factory.make("alice333", "V15Team", members={"bob333": "editor"})

    # bob id
    members_resp = await client.get(
        f"/api/teams/{team_id}/members", headers=alice_headers
    )
    bob_id = next(
        m["user_id"] for m in members_resp.json()["items"]
        if m["username"] == "bob333"
    )

    # 改角色: editor → manager
    r = await client.put(
        f"/api/teams/{team_id}/members/{bob_id}",
        headers=alice_headers, json={"role": "manager"},
    )
    assert r.status_code == 200, r.text

    # 查动态
    r = await client.get(
        f"/api/teams/{team_id}/activities?event_type=team_member_role_changed",
        headers=alice_headers,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    msg = items[0]["detail_message"]
    assert "alice333" in msg
    assert "bob333" in msg
    assert "可编辑" in msg
    assert "可管理" in msg
    assert "调整" in msg or "变更为" in msg or "改为" in msg
    # 不应含英文
    assert "old_role" not in msg
    assert "new_role" not in msg
    assert "editor" not in msg
    assert "manager" not in msg.replace("可管理", "")  # 注意「manager」英文不应出现
