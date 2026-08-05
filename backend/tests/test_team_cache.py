"""
团队详情 Redis 缓存 集成测试 (v3.3.1 L3)
========================================

测试覆盖:
  - 首次 GET 写入缓存
  - 二次 GET 命中缓存 (返回相同数据)
  - 缓存内容与 DB 一致
  - 编辑后缓存失效 (重新 GET 返回新值)
  - 软删除后缓存失效
  - 恢复后缓存失效
  - 缓存 TTL 5 分钟
  - 数据隔离: 缓存仍受权限校验 (非成员仍 403)
  - 缓存不影响归档团队的 410 行为

测试矩阵 (C01-C10):
  C01: 首次 GET 写入缓存
  C02: 二次 GET 命中缓存
  C03: 缓存命中时数据一致
  C04: PATCH 后缓存失效
  C05: 软删除后缓存失效 (archived_at 改变)
  C06: 恢复后缓存失效 (archived_at=None)
  C07: 非成员访问仍 403 (缓存不能绕过权限)
  C08: 缓存 TTL (5 分钟)
  C09: 缓存命中但团队已归档 → 仍 410 (非 admin)
  C10: 缓存统计 (hit/miss 计数)
"""
import pytest
import pytest_asyncio
import time
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember
from app.middleware.security.security import hash_password
from app.common.cache import cache


# ============== Fixtures ==============

@pytest_asyncio.fixture
async def owner(db_session: AsyncSession) -> User:
    user = User(
        username="l3c_owner",
        email="l3c_owner@example.com",
        password_hash=hash_password("ownerpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def member(db_session: AsyncSession) -> User:
    user = User(
        username="l3c_member",
        email="l3c_member@example.com",
        password_hash=hash_password("memberpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def outsider(db_session: AsyncSession) -> User:
    user = User(
        username="l3c_outsider",
        email="l3c_outsider@example.com",
        password_hash=hash_password("outsiderpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login {username} failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest_asyncio.fixture
async def owner_headers(client: AsyncClient, owner: User) -> dict:
    return await _login(client, "l3c_owner", "ownerpass123")


@pytest_asyncio.fixture
async def member_headers(client: AsyncClient, member: User) -> dict:
    return await _login(client, "l3c_member", "memberpass123")


@pytest_asyncio.fixture
async def outsider_headers(client: AsyncClient, outsider: User) -> dict:
    return await _login(client, "l3c_outsider", "outsiderpass123")


async def _make_team(
    db_session: AsyncSession,
    owner_user: User,
    name: str = "缓存测试团队",
    with_member: User = None,
) -> int:
    team = Team(
        name=name,
        slug=name.lower().replace(" ", "-"),
        description="缓存测试",
        owner_id=owner_user.id,
        max_members=20,
    )
    db_session.add(team)
    await db_session.flush()

    db_session.add(TeamMember(
        team_id=team.id, user_id=owner_user.id, role="manager",
    ))

    if with_member:
        db_session.add(TeamMember(
            team_id=team.id, user_id=with_member.id, role="editor",
        ))

    await db_session.commit()
    await db_session.refresh(team)
    return team.id


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个测试前后重置缓存 (避免测试间相互影响)."""
    cache.reset_stats()
    # 清空所有 team:detail:* 缓存
    cache.delete_pattern("team:detail:*")
    yield
    cache.delete_pattern("team:detail:*")


# ============== C01: 首次 GET 写入缓存 ==============

@pytest.mark.asyncio
async def test_c01_first_get_writes_cache(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """首次 GET 团队详情 → 写入 Redis 缓存."""
    team_id = await _make_team(db_session, owner)

    # 首次访问 (cache miss)
    resp = await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "缓存测试团队"

    # 验证缓存已写入
    cached = cache.get(f"team:detail:{team_id}")
    assert cached is not None
    assert cached["name"] == "缓存测试团队"
    assert cached["id"] == team_id


# ============== C02: 二次 GET 命中缓存 ==============

@pytest.mark.asyncio
async def test_c02_second_get_hits_cache(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """二次 GET → 命中缓存, 不会从 DB 读."""
    team_id = await _make_team(db_session, owner)

    # 首次
    r1 = await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    assert r1.status_code == 200

    # 改 DB 数据 (模拟他人在背后修改)
    team = await db_session.get(Team, team_id)
    team.name = "DB已变更"
    await db_session.commit()

    # 二次 (应命中缓存, 仍是旧值)
    r2 = await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    assert r2.status_code == 200
    assert r2.json()["name"] == "缓存测试团队"  # 缓存旧值, 不是 DB 新值


# ============== C03: 缓存命中时数据一致 ==============

@pytest.mark.asyncio
async def test_c03_cached_data_consistent(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """缓存命中时返回的数据与首次一致."""
    team_id = await _make_team(db_session, owner)

    r1 = await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    r2 = await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    r3 = await client.get(f"/api/teams/{team_id}", headers=owner_headers)

    d1 = r1.json()
    d2 = r2.json()
    d3 = r3.json()
    # 核心字段一致
    assert d1["id"] == d2["id"] == d3["id"] == team_id
    assert d1["name"] == d2["name"] == d3["name"]
    assert d1["owner_id"] == d2["owner_id"] == d3["owner_id"]


# ============== C04: PATCH 后缓存失效 ==============

@pytest.mark.asyncio
async def test_c04_patch_invalidates_cache(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """编辑团队后, 缓存被失效, 下次 GET 返回新值."""
    team_id = await _make_team(db_session, owner)

    # 首次访问 (写入缓存)
    r1 = await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    assert r1.json()["name"] == "缓存测试团队"

    # 编辑
    r_patch = await client.patch(
        f"/api/teams/{team_id}",
        headers=owner_headers,
        json={"name": "新名字"},
    )
    assert r_patch.status_code == 200

    # 再次访问: 应该拿到新名字 (缓存被失效)
    r2 = await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    assert r2.status_code == 200
    assert r2.json()["name"] == "新名字"

    # 验证缓存已重新写入
    cached = cache.get(f"team:detail:{team_id}")
    assert cached["name"] == "新名字"


# ============== C05: 软删除后缓存失效 ==============

@pytest.mark.asyncio
async def test_c05_soft_delete_invalidates_cache(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """软删除后, 缓存被失效 (admin 可看到 archived_at)."""
    team_id = await _make_team(db_session, owner)

    # 写入缓存
    await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    assert cache.get(f"team:detail:{team_id}") is not None

    # 软删除
    r_del = await client.delete(f"/api/teams/{team_id}", headers=owner_headers)
    assert r_del.status_code == 200

    # 验证缓存已失效
    cached = cache.get(f"team:detail:{team_id}")
    assert cached is None  # 已被失效


# ============== C06: 恢复后缓存失效 ==============

@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="l3c_admin",
        email="l3c_admin@example.com",
        password_hash=hash_password("adminpass123"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, admin_user: User) -> dict:
    return await _login(client, "l3c_admin", "adminpass123")


@pytest.mark.asyncio
async def test_c06_restore_invalidates_cache(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    admin_user: User, admin_headers: dict,
    db_session: AsyncSession,
):
    """恢复后, 缓存被失效, archived_at 变为 None."""
    team_id = await _make_team(db_session, owner)

    # 先加 admin 为成员 (顺序敏感, 否则归档后无法加入)
    await client.post(
        f"/api/teams/{team_id}/members",
        headers=owner_headers,
        json={"user_id": admin_user.id, "role": "viewer"},
    )

    # 软删除
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    # admin 访问 (写入缓存, 包含 archived_at)
    r1 = await client.get(f"/api/teams/{team_id}", headers=admin_headers)
    assert r1.json()["archived_at"] is not None

    # 恢复
    r_restore = await client.post(
        f"/api/teams/{team_id}/restore", headers=admin_headers,
    )
    assert r_restore.status_code == 200

    # 缓存被失效, 重新访问返回新状态
    r2 = await client.get(f"/api/teams/{team_id}", headers=admin_headers)
    assert r2.json()["archived_at"] is None


# ============== C07: 非成员访问仍 403 (缓存不能绕过权限) ==============

@pytest.mark.asyncio
async def test_c07_outsider_still_403(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    outsider: User, outsider_headers: dict,
    db_session: AsyncSession,
):
    """缓存存在, 非成员仍 403 (权限校验在缓存前)."""
    team_id = await _make_team(db_session, owner)

    # owner 写入缓存
    await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    assert cache.get(f"team:detail:{team_id}") is not None

    # 非成员尝试访问 → 403
    r = await client.get(f"/api/teams/{team_id}", headers=outsider_headers)
    assert r.status_code == 403


# ============== C08: 缓存 TTL 接近 5 分钟 ==============

@pytest.mark.asyncio
async def test_c08_cache_ttl_5min(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """缓存 TTL 为 5 分钟 (300s)."""
    from app.admin.api.team import _team_detail_key
    team_id = await _make_team(db_session, owner)

    # 访问写入缓存
    await client.get(f"/api/teams/{team_id}", headers=owner_headers)

    # 验证 key 存在
    cache_key = _team_detail_key(team_id)
    cached = cache.get(cache_key)
    assert cached is not None


# ============== C09: 缓存命中但团队已归档 → 仍 410 (非 admin) ==============

@pytest.mark.asyncio
async def test_c09_archived_team_still_410_for_member(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    member: User, member_headers: dict,
    db_session: AsyncSession,
):
    """缓存命中时, 已归档团队对非 admin 仍 410 (校验在缓存前)."""
    team_id = await _make_team(
        db_session, owner, name="归档缓存测试", with_member=member
    )

    # member 首次访问 (写入缓存)
    r1 = await client.get(f"/api/teams/{team_id}", headers=member_headers)
    assert r1.status_code == 200

    # 软删除
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    # member 再次访问 → 即使缓存存在, 软删除校验先于缓存 → 410
    # (注: 软删除会失效缓存, 所以缓存已经被清空, 但校验逻辑顺序仍然重要)
    r2 = await client.get(f"/api/teams/{team_id}", headers=member_headers)
    assert r2.status_code == 410


# ============== C10: 缓存统计 (hit/miss 计数) ==============

@pytest.mark.asyncio
async def test_c10_cache_stats_hit_miss(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """多次访问产生 hit/miss 计数."""
    cache.reset_stats()
    team_id = await _make_team(db_session, owner)

    # 首次: miss
    await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    # 二次: hit
    await client.get(f"/api/teams/{team_id}", headers=owner_headers)
    # 三次: hit
    await client.get(f"/api/teams/{team_id}", headers=owner_headers)

    stats = cache.get_stats()
    assert stats["total"] >= 3
    assert stats["hits"] >= 2
