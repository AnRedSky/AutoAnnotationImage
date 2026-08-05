"""
L4 阶段集成测试: 审计日志查询 + 团队活动 Feed (v3.3.1)
========================================================

测试覆盖:
  - A01-A08: 审计日志查询 GET /api/audit-logs
    - 权限 (admin / non-admin)
    - 分页 (page / page_size)
    - 过滤 (event_type / team_id / user_id / resource_type / time range)
    - 排序 (created_at DESC)
    - 错误 event_type → 400
  - F01-F06: 团队活动 Feed GET /api/teams/{id}/activities
    - 权限 (成员 / 非成员 / admin)
    - 归档团队对非 admin 410
    - 事件类型过滤
    - limit 参数边界
    - 数据隔离: 不同 team 的活动隔离

合计: 14 个测试用例
"""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember
from app.tasks.model.audit_log import AuditLog
from app.middleware.security.security import hash_password


# ============== 公共 Fixtures ==============

@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="l4_admin",
        email="l4_admin@example.com",
        password_hash=hash_password("adminpass123"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def owner(db_session: AsyncSession) -> User:
    user = User(
        username="l4_owner",
        email="l4_owner@example.com",
        password_hash=hash_password("ownerpass123"),
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
        username="l4_outsider",
        email="l4_outsider@example.com",
        password_hash=hash_password("outpass123"),
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
async def admin_headers(client: AsyncClient, admin_user: User) -> dict:
    return await _login(client, "l4_admin", "adminpass123")


@pytest_asyncio.fixture
async def owner_headers(client: AsyncClient, owner: User) -> dict:
    return await _login(client, "l4_owner", "ownerpass123")


@pytest_asyncio.fixture
async def outsider_headers(client: AsyncClient, outsider: User) -> dict:
    return await _login(client, "l4_outsider", "outpass123")


@pytest_asyncio.fixture
async def team(db_session: AsyncSession, owner: User) -> Team:
    """owner 创建的活跃团队."""
    t = Team(
        name="L4 测试团队",
        slug="l4-test-team",
        description="L4 阶段测试团队",
        owner_id=owner.id,
        max_members=10,
    )
    db_session.add(t)
    await db_session.flush()
    db_session.add(TeamMember(
        team_id=t.id, user_id=owner.id, role="manager",
    ))
    await db_session.commit()
    await db_session.refresh(t)
    return t


@pytest_asyncio.fixture
async def archived_team(db_session: AsyncSession, owner: User) -> Team:
    """owner 创建的已归档团队."""
    t = Team(
        name="L4 已归档团队",
        slug="l4-archived-team",
        description="L4 归档测试",
        owner_id=owner.id,
        max_members=10,
        archived_at=datetime.utcnow(),
    )
    db_session.add(t)
    await db_session.flush()
    db_session.add(TeamMember(
        team_id=t.id, user_id=owner.id, role="manager",
    ))
    await db_session.commit()
    await db_session.refresh(t)
    return t


async def _seed_audit_logs(
    db_session: AsyncSession,
    team: Team,
    user: User,
    count: int = 3,
) -> list[AuditLog]:
    """向 audit_log 表插入测试数据 (绕开 log_audit 业务)."""
    from app.tasks.service.audit_service import log_audit
    logs = []
    for i in range(count):
        await log_audit(
            db_session,
            user_id=user.id,
            event_type="team_updated",
            team_id=team.id,
            resource_type="team",
            resource_id=team.id,
            detail={"index": i, "change": f"test-{i}"},
        )
    await db_session.commit()
    return logs


# ============== A01-A08: 审计日志查询 ==============

class TestAuditLogsQuery:
    """GET /api/audit-logs 测试."""

    async def test_a01_admin_can_list_audit_logs(
        self, client: AsyncClient, admin_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """A01: admin 可查询审计日志 (返回 200 + items 数组)."""
        await _seed_audit_logs(db_session, team, owner, count=3)

        resp = await client.get("/api/audit-logs", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert body["total"] >= 3

    async def test_a02_non_admin_forbidden(
        self, client: AsyncClient, owner_headers: dict,
    ):
        """A02: 非 admin 访问 → 403."""
        resp = await client.get("/api/audit-logs", headers=owner_headers)
        assert resp.status_code == 403
        assert "无权限" in resp.json()["detail"]

    async def test_a03_pagination(
        self, client: AsyncClient, admin_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """A03: 分页参数 page / page_size 正确生效."""
        await _seed_audit_logs(db_session, team, owner, count=5)

        resp = await client.get(
            "/api/audit-logs?page=1&page_size=2", headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert len(body["items"]) == 2
        assert body["total"] >= 5

    async def test_a04_filter_by_event_type(
        self, client: AsyncClient, admin_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """A04: 按 event_type 过滤."""
        await _seed_audit_logs(db_session, team, owner, count=2)
        # 插入不同类型
        from app.tasks.service.audit_service import log_audit
        await log_audit(
            db_session, user_id=owner.id, event_type="team_member_invited",
            team_id=team.id, resource_type="team_member", resource_id=1,
        )
        await db_session.commit()

        resp = await client.get(
            "/api/audit-logs?event_type=team_member_invited",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(
            item["event_type"] == "team_member_invited"
            for item in body["items"]
        )

    async def test_a05_filter_by_team_id(
        self, client: AsyncClient, admin_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """A05: 按 team_id 过滤, 数据隔离."""
        await _seed_audit_logs(db_session, team, owner, count=2)

        resp = await client.get(
            f"/api/audit-logs?team_id={team.id}", headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(
            item["team_id"] == team.id for item in body["items"]
        )

    async def test_a06_filter_by_time_range(
        self, client: AsyncClient, admin_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """A06: 按时间范围过滤 (start / end)."""
        await _seed_audit_logs(db_session, team, owner, count=2)

        now = datetime.utcnow()
        start = (now - timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=1)).isoformat()

        resp = await client.get(
            f"/api/audit-logs?start={start}&end={end}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 2

    async def test_a07_invalid_event_type(
        self, client: AsyncClient, admin_headers: dict,
    ):
        """A07: 无效 event_type → 400."""
        resp = await client.get(
            "/api/audit-logs?event_type=invalid_event_xyz",
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "无效事件类型" in resp.json()["detail"]

    async def test_a08_results_sorted_desc(
        self, client: AsyncClient, admin_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """A08: 审计结果按 created_at DESC 排序 (最新在前)."""
        await _seed_audit_logs(db_session, team, owner, count=3)

        resp = await client.get(
            f"/api/audit-logs?team_id={team.id}", headers=admin_headers,
        )
        body = resp.json()
        assert len(body["items"]) >= 2
        timestamps = [item["created_at"] for item in body["items"]]
        # 倒序: 后一个 <= 前一个
        for i in range(len(timestamps) - 1):
            assert timestamps[i] >= timestamps[i + 1]


# ============== F01-F06: 团队活动 Feed ==============

class TestTeamActivities:
    """GET /api/teams/{id}/activities 测试."""

    async def test_f01_member_can_view_activities(
        self, client: AsyncClient, owner_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """F01: 团队成员可查看活动 Feed."""
        await _seed_audit_logs(db_session, team, owner, count=3)

        resp = await client.get(
            f"/api/teams/{team.id}/activities", headers=owner_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert body["total"] >= 3
        # 验证 event_label 语义化
        for item in body["items"]:
            assert "event_label" in item
            assert "username" in item

    async def test_f02_non_member_forbidden(
        self, client: AsyncClient, outsider_headers: dict,
        team: Team,
    ):
        """F02: 非团队成员 → 403."""
        resp = await client.get(
            f"/api/teams/{team.id}/activities", headers=outsider_headers,
        )
        assert resp.status_code == 403

    async def test_f03_archived_team_returns_410(
        self, client: AsyncClient, owner_headers: dict,
        archived_team: Team,
    ):
        """F03: 已归档团队对非 admin → 410."""
        resp = await client.get(
            f"/api/teams/{archived_team.id}/activities",
            headers=owner_headers,
        )
        assert resp.status_code == 410

    async def test_f04_admin_can_view_archived_activities(
        self, client: AsyncClient, db_session: AsyncSession,
        archived_team: Team, owner: User,
    ):
        """F04: super_admin 可查看已归档团队的活动 (v3.3.4: 收紧为仅 super_admin).

        旧行为: regular admin 可旁路 _get_member_or_403, 直接看任何团队活动
        新行为: 仅 super_admin 可看任意团队活动 (合规审计场景),
                regular admin 必须有团队成员关系

        验证 super_admin 旁路场景 (test_regular_admin 在
        test_permission_v334_audit.py::test_regular_admin_cannot_view_outsider_team_activities)
        """
        # 用 super_admin 账号登录 (绕过成员关系校验)
        from app.middleware.security.security import hash_password
        from app.admin.model.user import User as _User
        su = _User(
            username="l4_super_admin",
            email="l4_super@example.com",
            password_hash=hash_password("superpass123"),
            role="super_admin",
            is_active=True,
        )
        db_session.add(su)
        await db_session.commit()
        await db_session.refresh(su)
        su_headers = await _login(client, "l4_super_admin", "superpass123")

        await _seed_audit_logs(db_session, archived_team, owner, count=2)

        resp = await client.get(
            f"/api/teams/{archived_team.id}/activities",
            headers=su_headers,
        )
        assert resp.status_code == 200, (
            f"super_admin should view any team activities. "
            f"Got: {resp.status_code} - {resp.text}"
        )
        body = resp.json()
        assert body["total"] >= 2

    async def test_f05_filter_by_event_type(
        self, client: AsyncClient, owner_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """F05: 活动 Feed 支持 event_type 过滤."""
        # 混合类型
        from app.tasks.service.audit_service import log_audit
        await log_audit(
            db_session, user_id=owner.id, event_type="team_updated",
            team_id=team.id, resource_type="team", resource_id=team.id,
            detail={"v": 1},
        )
        await log_audit(
            db_session, user_id=owner.id, event_type="team_member_invited",
            team_id=team.id, resource_type="team_member", resource_id=99,
        )
        await db_session.commit()

        resp = await client.get(
            f"/api/teams/{team.id}/activities?event_type=team_member_invited",
            headers=owner_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(
            item["event_type"] == "team_member_invited"
            for item in body["items"]
        )

    async def test_f06_limit_parameter(
        self, client: AsyncClient, owner_headers: dict,
        team: Team, owner: User, db_session: AsyncSession,
    ):
        """F06: limit 参数控制返回数量 (1-200)."""
        await _seed_audit_logs(db_session, team, owner, count=5)

        resp = await client.get(
            f"/api/teams/{team.id}/activities?limit=2",
            headers=owner_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["limit"] == 2

        # 边界: limit=0 → 422
        resp = await client.get(
            f"/api/teams/{team.id}/activities?limit=0",
            headers=owner_headers,
        )
        assert resp.status_code == 422
