"""
团队统计端点集成测试 (v3.3.1 L2)
=================================

测试 GET /api/stats/team/{team_id} 端点:
  - 权限校验 (非成员 → 403, 成员 → 200)
  - 团队存在性 (404)
  - 基础字段 (team_id, team_name, member_count, dataset_count)
  - 图片概览 (image_total, labeled_total, unqualified_count)
  - 状态分布 (status_counts)
  - 贡献者 Top 10 (按 annotation 数倒序)
  - 每日趋势 (timeline 长度 = days, 0 填充缺失日期)
  - AI 节省时间估算
  - 类别分布
  - 多数据集聚合
  - days 参数控制时间范围

测试矩阵 (T01-T10):
  T01: 非成员访问 → 403
  T02: 成员访问空团队 → 200 + 全 0
  T03: 团队不存在 → 404
  T04: 单数据集团队 → 200 + 正确聚合
  T05: 多数据集团队 → 聚合正确
  T06: 不合格图片正确分离 (不计入 status_counts, 进入 unqualified_count)
  T07: 贡献者按 annotation 数量倒序
  T08: 每日趋势长度 = days, 缺失日期填 0
  T09: days 参数边界 (1, 90)
  T10: admin 不在成员表 → 403 (数据隔离)
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
from app.tasks.model.image import Image
from app.tasks.model.category import Category
from app.tasks.model.annotation_log import AnnotationLog
from app.middleware.security.security import hash_password


# ============== Fixtures ==============

@pytest_asyncio.fixture
async def owner(db_session: AsyncSession) -> User:
    """团队 owner"""
    user = User(
        username="team_owner",
        email="owner@example.com",
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
    """团队成员 editor"""
    user = User(
        username="team_member",
        email="member@example.com",
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
    """非团队成员用户"""
    user = User(
        username="outsider",
        email="out@example.com",
        password_hash=hash_password("outpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """系统管理员 (但不在任何团队)"""
    user = User(
        username="sysadmin",
        email="admin@example.com",
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
    return await _login(client, "team_owner", "ownerpass123")


@pytest_asyncio.fixture
async def member_headers(client: AsyncClient, member: User) -> dict:
    return await _login(client, "team_member", "memberpass123")


@pytest_asyncio.fixture
async def outsider_headers(client: AsyncClient, outsider: User) -> dict:
    return await _login(client, "outsider", "outpass123")


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, admin_user: User) -> dict:
    return await _login(client, "sysadmin", "adminpass123")


async def _make_team(
    db_session: AsyncSession,
    owner_user: User,
    name: str = "测试团队",
    members: dict = None,
) -> int:
    """创建团队 + 拉人. members: {username: role}"""
    team = Team(
        name=name,
        slug=name.lower().replace(" ", "-"),
        description="测试用团队",
        owner_id=owner_user.id,
        max_members=20,
    )
    db_session.add(team)
    await db_session.flush()

    # owner 自动加入
    db_session.add(TeamMember(
        team_id=team.id, user_id=owner_user.id, role="manager",
    ))

    if members:
        for username, role in members.items():
            result = await db_session.execute(
                select(User).where(User.username == username)
            )
            u = result.scalar_one()
            db_session.add(TeamMember(
                team_id=team.id, user_id=u.id, role=role,
            ))

    await db_session.commit()
    return team.id


async def _make_dataset(
    db_session: AsyncSession,
    team_id: int,
    owner_user: User,
    name: str = "测试数据集",
    image_status_distribution: dict = None,
    unqualified_count: int = 0,
    categories: list = None,
    label_map: dict = None,
) -> int:
    """创建数据集 + 图片. image_status_distribution: {status: count}.

    label_map: {status: category_name} 关联图片 final_label_id 与类别, 用于
    验证 category_distribution 聚合 (LEFT JOIN 需 final_label_id 非空).
    """
    ds = Dataset(
        name=name,
        task_type="classification",
        owner_id=owner_user.id,
        team_id=team_id,
    )
    db_session.add(ds)
    await db_session.flush()

    # 类别预建
    cat_id_by_name: dict = {}
    if categories:
        for cat_name in categories:
            cat = Category(dataset_id=ds.id, name=cat_name)
            db_session.add(cat)
        await db_session.flush()
        rows = (await db_session.execute(
            select(Category).where(Category.dataset_id == ds.id)
        )).scalars().all()
        cat_id_by_name = {c.name: c.id for c in rows}

    # 创建图片
    if image_status_distribution:
        for status, count in image_status_distribution.items():
            cat_id = None
            if label_map and status in label_map:
                cat_id = cat_id_by_name.get(label_map[status])
            for i in range(count):
                db_session.add(Image(
                    dataset_id=ds.id,
                    filename=f"img_{status}_{i}.jpg",
                    storage_path=f"/fake/{status}_{i}.jpg",
                    file_size=1024,
                    file_hash=f"hash_{status}_{i}_{ds.id}",
                    status=status,
                    quality_flag=None,
                    final_label_id=cat_id,
                ))

    # 不合格图片
    for i in range(unqualified_count):
        db_session.add(Image(
            dataset_id=ds.id,
            filename=f"img_unqual_{i}.jpg",
            storage_path=f"/fake/unqual_{i}.jpg",
            file_size=1024,
            file_hash=f"hash_unqual_{i}_{ds.id}",
            status="pending",
            quality_flag="unqualified",
        ))

    await db_session.commit()
    return ds.id


async def _make_annotation_log(
    db_session: AsyncSession,
    user_id: int,
    team_id: int,
    image_id: int,
    time_spent_ms: int = 5000,
    created_at: datetime = None,
) -> int:
    """写入标注日志 (用于贡献者排名/趋势统计)"""
    log = AnnotationLog(
        image_id=image_id,
        user_id=user_id,
        team_id=team_id,
        action="confirm",
        time_spent_ms=time_spent_ms,
        created_at=created_at or datetime.utcnow(),
    )
    db_session.add(log)
    await db_session.commit()
    return log.id


# ============== T01: 非成员访问 → 403 ==============

@pytest.mark.asyncio
async def test_t01_non_member_access_forbidden(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    outsider: User, outsider_headers: dict,
    db_session: AsyncSession,
):
    """非团队成员访问 → 403 (数据隔离, 包括 admin)."""
    team_id = await _make_team(db_session, owner)

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=outsider_headers,
    )
    assert resp.status_code == 403
    assert "非团队成员" in resp.json()["detail"]


# ============== T02: 成员访问空团队 → 200 + 全 0 ==============

@pytest.mark.asyncio
async def test_t02_member_access_empty_team(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """成员访问空团队 (无数据集/无标注) → 200 + 全 0 / 空数组."""
    team_id = await _make_team(db_session, owner)

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["team_id"] == team_id
    assert data["team_name"] == "测试团队"
    assert data["member_count"] == 1
    assert data["dataset_count"] == 0
    assert data["image_total"] == 0
    assert data["labeled_total"] == 0
    assert data["unqualified_count"] == 0
    assert data["status_counts"] == {}
    assert data["category_distribution"] == {}
    assert data["top_contributors"] == []
    assert data["timeline"]["days"] == 7
    assert len(data["timeline"]["data"]) == 7
    assert all(d["count"] == 0 for d in data["timeline"]["data"])


# ============== T03: 团队不存在 → 404 ==============

@pytest.mark.asyncio
async def test_t03_team_not_found(
    client: AsyncClient, owner_headers: dict,
):
    """team_id 不存在 → 404."""
    resp = await client.get(
        "/api/stats/team/99999",
        headers=owner_headers,
    )
    assert resp.status_code == 404
    assert "Team not found" in resp.json()["detail"]


# ============== T04: 单数据集团队 → 200 + 正确聚合 ==============

@pytest.mark.asyncio
async def test_t04_single_dataset_aggregation(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """单数据集: 验证 image_total / labeled_total / status_counts."""
    team_id = await _make_team(db_session, owner)
    await _make_dataset(
        db_session, team_id, owner,
        image_status_distribution={
            "pending": 5,
            "ai_labeled": 3,
            "human_confirmed": 2,
        },
        unqualified_count=1,
    )

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["dataset_count"] == 1
    assert data["image_total"] == 11  # 5+3+2+1(unqualified)
    assert data["labeled_total"] == 2  # 仅 human_confirmed
    assert data["unqualified_count"] == 1
    # status_counts 不含 unqualified (1)
    assert data["status_counts"] == {
        "pending": 5,
        "ai_labeled": 3,
        "human_confirmed": 2,
    }


# ============== T05: 多数据集团队 → 聚合正确 ==============

@pytest.mark.asyncio
async def test_t05_multi_dataset_aggregation(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """多数据集: 状态分布/不合格计数/类别分布全部合并."""
    team_id = await _make_team(db_session, owner)
    await _make_dataset(
        db_session, team_id, owner,
        name="DS1",
        image_status_distribution={"pending": 4, "human_confirmed": 6},
        unqualified_count=2,
        categories=["cat", "dog"],
        label_map={"pending": "cat", "human_confirmed": "dog"},
    )
    await _make_dataset(
        db_session, team_id, owner,
        name="DS2",
        image_status_distribution={"ai_labeled": 3, "human_corrected": 2},
        unqualified_count=1,
        categories=["cat", "bird"],
        label_map={"ai_labeled": "cat", "human_corrected": "bird"},
    )

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["dataset_count"] == 2
    assert data["image_total"] == 18  # 4+6+2+3+2+1
    assert data["labeled_total"] == 8   # 6 + 2
    assert data["unqualified_count"] == 3
    assert data["status_counts"] == {
        "pending": 4,
        "human_confirmed": 6,
        "ai_labeled": 3,
        "human_corrected": 2,
    }
    # 类别分布: cat=4(DS1 pending)+3(DS2 ai_labeled)=7, dog=6, bird=2
    assert data["category_distribution"]["cat"] == 7
    assert data["category_distribution"]["dog"] == 6
    assert data["category_distribution"]["bird"] == 2


# ============== T06: 不合格图片正确分离 ==============

@pytest.mark.asyncio
async def test_t06_unqualified_isolation(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """不合格图片不计入 status_counts, 仅记入 unqualified_count."""
    team_id = await _make_team(db_session, owner)
    await _make_dataset(
        db_session, team_id, owner,
        image_status_distribution={"pending": 2, "ai_labeled": 1},
        unqualified_count=5,  # 5 张不合格 (status=pending)
    )

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    # image_total = 全部 8 张
    assert data["image_total"] == 8
    # unqualified_count = 5
    assert data["unqualified_count"] == 5
    # status_counts 不含不合格 (只 2+1=3)
    assert data["status_counts"] == {
        "pending": 2,
        "ai_labeled": 1,
    }
    # labeled = 0 (没 human_confirmed 等)
    assert data["labeled_total"] == 0


# ============== T07: 贡献者按 annotation 数量倒序 ==============

@pytest.mark.asyncio
async def test_t07_top_contributors_ordering(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    member: User, member_headers: dict,
    db_session: AsyncSession,
):
    """贡献者按 annotation 数倒序, 含 username/avg_seconds."""
    team_id = await _make_team(
        db_session, owner, members={"team_member": "editor"}
    )
    ds_id = await _make_dataset(
        db_session, team_id, owner,
        image_status_distribution={"pending": 10},
    )

    # 取若干张图, 写入标注日志
    images = (await db_session.execute(
        select(Image).where(Image.dataset_id == ds_id).limit(7)
    )).scalars().all()

    # owner: 5 次标注, 每次 2000ms
    for i in range(5):
        await _make_annotation_log(
            db_session, owner.id, team_id, images[i].id,
            time_spent_ms=2000,
        )
    # member: 2 次标注, 每次 5000ms
    for i in range(5, 7):
        await _make_annotation_log(
            db_session, member.id, team_id, images[i].id,
            time_spent_ms=5000,
        )

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    contributors = resp.json()["top_contributors"]

    assert len(contributors) == 2
    # 排序: owner 5 次, member 2 次
    assert contributors[0]["username"] == "team_owner"
    assert contributors[0]["annotation_count"] == 5
    assert contributors[0]["total_seconds"] == 10.0
    assert contributors[0]["avg_seconds_per_annotation"] == 2.0

    assert contributors[1]["username"] == "team_member"
    assert contributors[1]["annotation_count"] == 2
    assert contributors[1]["total_seconds"] == 10.0
    assert contributors[1]["avg_seconds_per_annotation"] == 5.0


# ============== T08: 每日趋势长度 = days, 缺失日期填 0 ==============

@pytest.mark.asyncio
async def test_t08_daily_timeline_fill_zeros(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """timeline 长度 == days, 缺失日期 count=0."""
    team_id = await _make_team(db_session, owner)
    ds_id = await _make_dataset(
        db_session, team_id, owner,
        image_status_distribution={"pending": 5},
    )
    images = (await db_session.execute(
        select(Image).where(Image.dataset_id == ds_id)
    )).scalars().all()

    # 仅 1 天有标注 (今天)
    today = datetime.utcnow()
    await _make_annotation_log(
        db_session, owner.id, team_id, images[0].id,
        created_at=today,
    )

    resp = await client.get(
        f"/api/stats/team/{team_id}?days=7",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    timeline = resp.json()["timeline"]

    assert timeline["days"] == 7
    assert len(timeline["data"]) == 7
    # 最后一天 (今天) 应该是 1, 其他 6 天都是 0
    counts = [d["count"] for d in timeline["data"]]
    assert sum(counts) == 1
    assert counts[-1] == 1
    assert all(c == 0 for c in counts[:-1])

    # 日期连续, 跨度 7 天
    dates = [d["date"] for d in timeline["data"]]
    first = datetime.fromisoformat(dates[0])
    last = datetime.fromisoformat(dates[-1])
    assert (last - first).days == 6


# ============== T09: days 参数边界 ==============

@pytest.mark.asyncio
async def test_t09_days_param_boundary(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """days 范围 [1, 90], 默认 7."""
    team_id = await _make_team(db_session, owner)

    # 默认
    r1 = await client.get(
        f"/api/stats/team/{team_id}",
        headers=owner_headers,
    )
    assert r1.status_code == 200
    assert r1.json()["timeline"]["days"] == 7

    # 显式 30
    r2 = await client.get(
        f"/api/stats/team/{team_id}?days=30",
        headers=owner_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["timeline"]["days"] == 30
    assert len(r2.json()["timeline"]["data"]) == 30

    # 边界 1
    r3 = await client.get(
        f"/api/stats/team/{team_id}?days=1",
        headers=owner_headers,
    )
    assert r3.status_code == 200
    assert r3.json()["timeline"]["days"] == 1

    # 越界 0
    r4 = await client.get(
        f"/api/stats/team/{team_id}?days=0",
        headers=owner_headers,
    )
    assert r4.status_code == 422  # validation error

    # 越界 91
    r5 = await client.get(
        f"/api/stats/team/{team_id}?days=91",
        headers=owner_headers,
    )
    assert r5.status_code == 422


# ============== T10: admin 不在成员表 → 403 (数据隔离) ==============

@pytest.mark.asyncio
async def test_t10_admin_not_in_team_forbidden(
    client: AsyncClient,
    owner: User,
    admin_user: User, admin_headers: dict,
    db_session: AsyncSession,
):
    """系统管理员 (admin) 不在团队成员表 → 403 (团队数据严格隔离, admin 不绕过)."""
    team_id = await _make_team(db_session, owner)

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=admin_headers,
    )
    assert resp.status_code == 403


# ============== T11: editor 角色可访问 (任意成员均可) ==============

@pytest.mark.asyncio
async def test_t11_editor_can_access(
    client: AsyncClient,
    owner: User,
    member: User, member_headers: dict,
    db_session: AsyncSession,
):
    """editor 角色可访问统计 (read-only 性质, 不要求 manager)."""
    team_id = await _make_team(
        db_session, owner, members={"team_member": "editor"}
    )
    await _make_dataset(
        db_session, team_id, owner,
        image_status_distribution={"pending": 3},
    )

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=member_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["image_total"] == 3


# ============== T12: ai_saved 字段计算正确 ==============

@pytest.mark.asyncio
async def test_t12_ai_saved_calculation(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """ai_saved 字段: total_annotations/avg_seconds/estimated_saved_*."""
    team_id = await _make_team(db_session, owner)
    ds_id = await _make_dataset(
        db_session, team_id, owner,
        image_status_distribution={
            "ai_labeled": 10,      # AI 已标 10
            "human_confirmed": 5,  # 人工确认 5
        },
    )
    images = (await db_session.execute(
        select(Image).where(Image.dataset_id == ds_id)
    )).scalars().all()

    # 5 次人工确认日志, 每次 4s
    for i in range(5):
        await _make_annotation_log(
            db_session, owner.id, team_id, images[i].id,
            time_spent_ms=4000,
        )

    resp = await client.get(
        f"/api/stats/team/{team_id}",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    saved = resp.json()["ai_saved"]

    # total_annotations = 5
    assert saved["total_annotations"] == 5
    # actual_seconds = 5 * 4 = 20
    assert saved["actual_seconds"] == 20.0
    # avg_seconds_per_image = 20 / 5 = 4
    assert saved["avg_seconds_per_image"] == 4.0
    # baseline: (10 ai + 5 human) * 8s = 120s
    # saved = 120 - 20 = 100s
    assert saved["estimated_saved_seconds"] == 100.0
    # saved_ratio = 100/120 ≈ 0.8333
    assert abs(saved["estimated_saved_ratio"] - 0.8333) < 0.001
    assert saved["baseline_seconds_per_image"] == 8.0
