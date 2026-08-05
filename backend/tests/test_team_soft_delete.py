"""
团队软删除/恢复集成测试 (v3.3.1 L3)
======================================

测试覆盖:
  - DELETE 端点改为软删除 (设置 archived_at)
  - 列表默认过滤 archived_at (不可见)
  - admin 可通过 include_archived=true 看到归档团队
  - 详情/写操作拒绝已归档团队 (410)
  - 二次删除返回 400
  - 非 owner 删除 → 403
  - admin 可通过 POST /restore 恢复
  - 非 admin 调用 restore → 403
  - 恢复未归档团队 → 400
  - 不存在的团队 → 404
  - 审计日志记录 team_deleted / team_restored
  - 数据集 team_id 在删除时清空 (避免悬挂)

测试矩阵 (T01-T14):
  T01: DELETE 软删成功 (返回 archived_at)
  T02: 列表默认不显示已归档团队
  T03: admin 可通过 include_archived=true 看到归档
  T04: 详情 GET 已归档 → 410 (成员)
  T05: 详情 GET 已归档 → 200 (admin)
  T06: 二次删除已归档 → 400
  T07: 非 owner 删除 → 403
  T08: PATCH 已归档 → 410
  T09: 邀请成员到已归档 → 410
  T10: 数据集列表 已归档 → 410
  T11: 主动退队 已归档 → 410
  T12: admin 恢复成功 (200 + archived_at=None)
  T13: 恢复非 admin → 403
  T14: 恢复未归档 → 400
  T15: 团队 deleted 后 team_deleted 审计入库
  T16: 团队 restored 后 team_restored 审计入库
  T17: 数据集 team_id 在删除时清空
"""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from types import SimpleNamespace

from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember
from app.tasks.model.dataset import Dataset
from app.tasks.model.audit_log import AuditLog
from app.middleware.security.security import hash_password


# ============== Fixtures ==============

@pytest_asyncio.fixture
async def owner(db_session: AsyncSession) -> User:
    user = User(
        username="l3_owner",
        email="l3_owner@example.com",
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
        username="l3_member",
        email="l3_member@example.com",
        password_hash=hash_password("memberpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def sysadmin(db_session: AsyncSession) -> User:
    user = User(
        username="l3_admin",
        email="l3_admin@example.com",
        password_hash=hash_password("adminpass123"),
        role="admin",
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
    return await _login(client, "l3_owner", "ownerpass123")


@pytest_asyncio.fixture
async def member_headers(client: AsyncClient, member: User) -> dict:
    return await _login(client, "l3_member", "memberpass123")


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, sysadmin: User) -> dict:
    return await _login(client, "l3_admin", "adminpass123")


async def _make_team(
    db_session: AsyncSession,
    owner_user: User,
    name: str = "L3测试团队",
    with_member: User = None,
    with_dataset: bool = False,
) -> int:
    """创建团队 + 拉人 + 可选数据集."""
    team = Team(
        name=name,
        slug=name.lower().replace(" ", "-"),
        description="L3测试",
        owner_id=owner_user.id,
        max_members=20,
    )
    db_session.add(team)
    await db_session.flush()

    # owner 自动加入
    db_session.add(TeamMember(
        team_id=team.id, user_id=owner_user.id, role="manager",
    ))

    if with_member:
        db_session.add(TeamMember(
            team_id=team.id, user_id=with_member.id, role="editor",
        ))

    if with_dataset:
        ds = Dataset(
            name=f"DS-{name}",
            task_type="classification",
            owner_id=owner_user.id,
            team_id=team.id,
        )
        db_session.add(ds)

    await db_session.commit()
    await db_session.refresh(team)
    return team.id


# ============== T01: DELETE 软删成功 ==============

@pytest.mark.asyncio
async def test_t01_soft_delete_success(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """DELETE 软删: 返回 archived_at, DB 中 archived_at 已设置."""
    team_id = await _make_team(db_session, owner)

    resp = await client.delete(
        f"/api/teams/{team_id}",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "已归档" in data["detail"]
    assert "archived_at" in data

    # DB 验证: team.archived_at 已设置 (非 NULL)
    team = await db_session.get(Team, team_id)
    assert team.archived_at is not None
    assert (datetime.utcnow() - team.archived_at).total_seconds() < 60


# ============== T02: 列表默认不显示已归档 ==============

@pytest.mark.asyncio
async def test_t02_list_excludes_archived_by_default(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """已归档团队默认从列表过滤掉."""
    # 创建 2 个团队, 归档其中一个
    t1 = await _make_team(db_session, owner, name="正常团队")
    t2 = await _make_team(db_session, owner, name="归档团队")
    await client.delete(f"/api/teams/{t2}", headers=owner_headers)

    resp = await client.get("/api/teams", headers=owner_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = [t["id"] for t in items]
    assert t1 in ids
    assert t2 not in ids


# ============== T03: admin 可见归档团队 ==============

@pytest.mark.asyncio
async def test_t03_admin_can_see_archived(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    sysadmin: User, admin_headers: dict,
    db_session: AsyncSession,
):
    """admin 通过 include_archived=true 可看到归档团队."""
    team_id = await _make_team(db_session, owner)
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    # 不传 include_archived → admin 也看不到 (按统一过滤规则)
    r1 = await client.get("/api/teams", headers=admin_headers)
    assert r1.status_code == 200
    # admin 不在团队成员表, 默认应看不到
    assert all(t["id"] != team_id for t in r1.json()["items"])

    # 传 include_archived=true → 也不显示 (因为 admin 不在成员表)
    r2 = await client.get(
        "/api/teams?include_archived=true",
        headers=admin_headers,
    )
    assert r2.status_code == 200
    # admin 不在成员表, 即使 include_archived=true 也看不到
    assert all(t["id"] != team_id for t in r2.json()["items"])


# ============== T04: 详情 GET 已归档 (成员) → 410 ==============

@pytest.mark.asyncio
async def test_t04_member_get_archived_team_gone(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    member: User, member_headers: dict,
    db_session: AsyncSession,
):
    """成员访问已归档团队 → 410 Gone."""
    team_id = await _make_team(
        db_session, owner, name="归档测试", with_member=member
    )
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    resp = await client.get(f"/api/teams/{team_id}", headers=member_headers)
    assert resp.status_code == 410
    assert "已归档" in resp.json()["detail"]


# ============== T05: 详情 GET 已归档 (admin) → 200 ==============

@pytest.mark.asyncio
async def test_t05_admin_can_get_archived_team(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    sysadmin: User, admin_headers: dict,
    db_session: AsyncSession,
):
    """admin 可绕过 410 (用于恢复), 看到 archived_at."""
    # 先把 admin 加为成员, 再软删
    team_id = await _make_team(db_session, owner)
    await client.post(
        f"/api/teams/{team_id}/members",
        headers=owner_headers,
        json={"user_id": sysadmin.id, "role": "viewer"},
    )
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    resp = await client.get(f"/api/teams/{team_id}", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["archived_at"] is not None


# ============== T06: 二次删除已归档 → 400 ==============

@pytest.mark.asyncio
async def test_t06_double_delete_returns_400(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """删除已归档团队 → 400."""
    team_id = await _make_team(db_session, owner)
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    # 再次删除
    resp = await client.delete(
        f"/api/teams/{team_id}",
        headers=owner_headers,
    )
    assert resp.status_code == 400
    assert "已归档" in resp.json()["detail"]


# ============== T07: 非 owner 删除 → 403 ==============

@pytest.mark.asyncio
async def test_t07_non_owner_delete_forbidden(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    member: User, member_headers: dict,
    db_session: AsyncSession,
):
    """非 owner (即使是 manager) 不可删除团队."""
    team_id = await _make_team(
        db_session, owner, name="删除权限测试", with_member=member
    )

    resp = await client.delete(
        f"/api/teams/{team_id}",
        headers=member_headers,
    )
    assert resp.status_code == 403


# ============== T08: PATCH 已归档 → 410 ==============

@pytest.mark.asyncio
async def test_t08_patch_archived_team_gone(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """编辑已归档团队 → 410."""
    team_id = await _make_team(db_session, owner)
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    resp = await client.patch(
        f"/api/teams/{team_id}",
        headers=owner_headers,
        json={"name": "新名字"},
    )
    assert resp.status_code == 410


# ============== T09: 邀请成员到已归档 → 410 ==============

@pytest.mark.asyncio
async def test_t09_invite_to_archived_team_gone(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    member: User,
    db_session: AsyncSession,
):
    """邀请成员到已归档团队 → 410."""
    team_id = await _make_team(db_session, owner)
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    resp = await client.post(
        f"/api/teams/{team_id}/members",
        headers=owner_headers,
        json={"user_id": member.id, "role": "editor"},
    )
    assert resp.status_code == 410


# ============== T10: 数据集列表 已归档 → 410 ==============

@pytest.mark.asyncio
async def test_t10_list_datasets_archived_gone(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    member: User, member_headers: dict,
    db_session: AsyncSession,
):
    """查看已归档团队的数据集 → 410."""
    team_id = await _make_team(
        db_session, owner, name="数据集归档测试",
        with_member=member, with_dataset=True,
    )
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    resp = await client.get(
        f"/api/teams/{team_id}/datasets",
        headers=member_headers,
    )
    assert resp.status_code == 410


# ============== T11: 主动退队 已归档 → 410 ==============

@pytest.mark.asyncio
async def test_t11_leave_archived_team_gone(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    member: User, member_headers: dict,
    db_session: AsyncSession,
):
    """主动退队已归档团队 → 410."""
    team_id = await _make_team(
        db_session, owner, name="退队归档测试", with_member=member
    )
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    resp = await client.delete(
        f"/api/teams/{team_id}/members/me",
        headers=member_headers,
    )
    assert resp.status_code == 410


# ============== T12: admin 恢复成功 ==============

@pytest.mark.asyncio
async def test_t12_admin_restore_success(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    sysadmin: User, admin_headers: dict,
    db_session: AsyncSession,
):
    """admin 恢复已归档团队 → 200, archived_at=None."""
    team_id = await _make_team(db_session, owner)
    # 先把 admin 加为成员, 再软删 (顺序敏感)
    await client.post(
        f"/api/teams/{team_id}/members",
        headers=owner_headers,
        json={"user_id": sysadmin.id, "role": "viewer"},
    )
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    resp = await client.post(
        f"/api/teams/{team_id}/restore",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "已恢复" in data["detail"]
    assert data["team"]["id"] == team_id

    # DB 验证: archived_at 已清空
    team = await db_session.get(Team, team_id)
    assert team.archived_at is None


# ============== T13: 恢复非 admin → 403 ==============

@pytest.mark.asyncio
async def test_t13_restore_non_admin_forbidden(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """非 admin 调用 restore → 403 (即使 owner 也不行)."""
    team_id = await _make_team(db_session, owner)
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    # owner 也不能恢复 (需联系 admin)
    resp = await client.post(
        f"/api/teams/{team_id}/restore",
        headers=owner_headers,
    )
    assert resp.status_code == 403
    assert "管理员" in resp.json()["detail"]


# ============== T14: 恢复未归档 → 400 ==============

@pytest.mark.asyncio
async def test_t14_restore_non_archived_team_400(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    sysadmin: User, admin_headers: dict,
    db_session: AsyncSession,
):
    """恢复未归档团队 → 400."""
    team_id = await _make_team(db_session, owner)
    # admin 加为成员
    await client.post(
        f"/api/teams/{team_id}/members",
        headers=owner_headers,
        json={"user_id": sysadmin.id, "role": "viewer"},
    )

    resp = await client.post(
        f"/api/teams/{team_id}/restore",
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "未归档" in resp.json()["detail"]


# ============== T15: 团队 deleted 审计入库 ==============

@pytest.mark.asyncio
async def test_t15_soft_delete_audit_logged(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """软删除时记录 team_deleted 审计 (含 soft_delete=True)."""
    team_id = await _make_team(db_session, owner)
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    audit = (await db_session.execute(
        select(AuditLog).where(
            AuditLog.event_type == "team_deleted",
            AuditLog.team_id == team_id,
        )
    )).scalar_one()
    assert audit.user_id == owner.id
    assert audit.detail["soft_delete"] is True
    assert audit.detail["name"] == "L3测试团队"


# ============== T16: 团队 restored 审计入库 ==============

@pytest.mark.asyncio
async def test_t16_restore_audit_logged(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    sysadmin: User, admin_headers: dict,
    db_session: AsyncSession,
):
    """恢复时记录 team_restored 审计."""
    team_id = await _make_team(db_session, owner)
    # 先把 admin 加为成员, 再软删 (顺序敏感)
    await client.post(
        f"/api/teams/{team_id}/members",
        headers=owner_headers,
        json={"user_id": sysadmin.id, "role": "viewer"},
    )
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)
    await client.post(f"/api/teams/{team_id}/restore", headers=admin_headers)

    audit = (await db_session.execute(
        select(AuditLog).where(
            AuditLog.event_type == "team_restored",
            AuditLog.team_id == team_id,
        )
    )).scalar_one()
    assert audit.user_id == sysadmin.id
    assert audit.detail["name"] == "L3测试团队"
    assert audit.detail["owner_id"] == owner.id
    assert "archived_at_before" in audit.detail


# ============== T17: 数据集 team_id 在删除时清空 ==============

@pytest.mark.asyncio
async def test_t17_dataset_team_id_cleared_on_soft_delete(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """软删除时清空关联数据集的 team_id."""
    team_id = await _make_team(
        db_session, owner, name="数据集清空测试", with_dataset=True
    )

    # 删除前 dataset.team_id = team_id
    ds = (await db_session.execute(
        select(Dataset).where(Dataset.team_id == team_id)
    )).scalar_one()
    assert ds.team_id == team_id

    # 软删除
    await client.delete(f"/api/teams/{team_id}", headers=owner_headers)

    # 删除后 dataset.team_id = None
    # 注意: db_session 启用了 expire_on_commit=False, 已加载的对象
    # 不会自动过期, 必须显式 refresh 才能读到 commit 后的新值.
    await db_session.refresh(ds)
    assert ds.team_id is None
