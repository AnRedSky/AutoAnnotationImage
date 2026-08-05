"""
团队列表分页 + 搜索 + 排序 集成测试 (v3.3.1 L3)
==================================================

测试覆盖:
  - 默认分页 (page=1, page_size=20)
  - 自定义分页 (page=2, page_size=5)
  - 边界: page_size > 100 → 422
  - 边界: page=0 → 422
  - 搜索: name 模糊匹配
  - 搜索: description 模糊匹配
  - 搜索: 中文 + slug
  - 搜索: 空字符串 / 不存在关键词
  - 搜索 + 分页组合
  - 排序: name_asc / name_desc
  - 排序: created_asc / created_desc
  - 排序: member_count_desc
  - 排序: id_asc / id_desc (默认)
  - 排序: 非法 sort 参数 → 422
  - total / total_pages 正确性
  - 数据隔离: 搜索仅在我加入的团队中
  - 搜索 + 软删除组合: 归档不在搜索结果中

测试矩阵 (P01-P20):
  P01: 默认分页返回所有
  P02: page=1 + page_size=5
  P03: page=2 + page_size=5
  P04: page_size=0 → 422
  P05: page_size=101 → 422
  P06: page=0 → 422
  P07: 搜索 name 精确匹配
  P08: 搜索 name 子串匹配
  P09: 搜索 description 匹配
  P10: 搜索 slug 匹配
  P11: 搜索 + 分页组合
  P12: 搜索无结果返回空
  P13: 搜索空字符串 (视作无搜索)
  P14: 搜索中文关键词
  P15: 排序 name_asc
  P16: 排序 name_desc
  P17: 排序 member_count_desc
  P18: 排序 created_asc / created_desc
  P19: 非法 sort 参数 → 422
  P20: 归档团队不出现在搜索结果中
"""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember
from app.middleware.security.security import hash_password


# ============== Fixtures ==============

@pytest_asyncio.fixture
async def owner(db_session: AsyncSession) -> User:
    user = User(
        username="l3p_owner",
        email="l3p_owner@example.com",
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
        username="l3p_member",
        email="l3p_member@example.com",
        password_hash=hash_password("memberpass123"),
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
    return await _login(client, "l3p_owner", "ownerpass123")


@pytest_asyncio.fixture
async def member_headers(client: AsyncClient, member: User) -> dict:
    return await _login(client, "l3p_member", "memberpass123")


async def _make_team(
    db_session: AsyncSession,
    owner_user: User,
    name: str,
    description: str = "",
    with_members: int = 0,
    members: list = None,
) -> int:
    """创建团队 + 可选邀请成员.

    with_members: 自动创建 x 个虚拟成员加入团队
    members: 直接加入的 User 列表
    """
    team = Team(
        name=name,
        slug=name.lower().replace(" ", "-"),
        description=description,
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
        for m in members:
            db_session.add(TeamMember(
                team_id=team.id, user_id=m.id, role="editor",
            ))

    if with_members > 0:
        for i in range(with_members):
            tmp = User(
                username=f"tmp_user_{team.id}_{i}",
                email=f"tmp_{team.id}_{i}@example.com",
                password_hash=hash_password("tmp123"),
                role="annotator",
                is_active=True,
            )
            db_session.add(tmp)
            await db_session.flush()
            db_session.add(TeamMember(
                team_id=team.id, user_id=tmp.id, role="editor",
            ))

    await db_session.commit()
    await db_session.refresh(team)
    return team.id


# ============== P01: 默认分页 ==============

@pytest.mark.asyncio
async def test_p01_default_pagination(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """默认 page=1, page_size=20, 全部返回 (小于 20)."""
    for i in range(3):
        await _make_team(db_session, owner, name=f"默认团队{i}")

    resp = await client.get("/api/teams", headers=owner_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 3
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 3
    assert data["total_pages"] == 1
    assert data["sort"] == "id_desc"  # 默认


# ============== P02: page=1, page_size=5 ==============

@pytest.mark.asyncio
async def test_p02_custom_page_size(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """自定义 page_size=5, 7 个团队 → 5 条 + total=7."""
    for i in range(7):
        await _make_team(db_session, owner, name=f"分页测试{i}")

    resp = await client.get(
        "/api/teams?page=1&page_size=5", headers=owner_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 5
    assert data["total"] == 7
    assert data["total_pages"] == 2
    assert data["page"] == 1
    assert data["page_size"] == 5


# ============== P03: page=2, page_size=5 ==============

@pytest.mark.asyncio
async def test_p03_second_page(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """第 2 页: 7 个团队, page_size=5 → 返回剩余 2 条."""
    for i in range(7):
        await _make_team(db_session, owner, name=f"二页测试{i}")

    resp = await client.get(
        "/api/teams?page=2&page_size=5", headers=owner_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["page"] == 2


# ============== P04: page_size=0 → 422 ==============

@pytest.mark.asyncio
async def test_p04_page_size_zero_invalid(
    client: AsyncClient,
    owner_headers: dict,
):
    """page_size=0 → 422."""
    resp = await client.get(
        "/api/teams?page_size=0", headers=owner_headers,
    )
    assert resp.status_code == 422


# ============== P05: page_size=101 → 422 ==============

@pytest.mark.asyncio
async def test_p05_page_size_too_large(
    client: AsyncClient,
    owner_headers: dict,
):
    """page_size > 100 → 422."""
    resp = await client.get(
        "/api/teams?page_size=101", headers=owner_headers,
    )
    assert resp.status_code == 422


# ============== P06: page=0 → 422 ==============

@pytest.mark.asyncio
async def test_p06_page_zero_invalid(
    client: AsyncClient,
    owner_headers: dict,
):
    """page=0 → 422."""
    resp = await client.get(
        "/api/teams?page=0", headers=owner_headers,
    )
    assert resp.status_code == 422


# ============== P07: 搜索 name 精确匹配 ==============

@pytest.mark.asyncio
async def test_p07_search_exact_name(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """search 精确匹配 name."""
    await _make_team(db_session, owner, name="AI标注组")
    await _make_team(db_session, owner, name="数据清洗组")

    resp = await client.get(
        "/api/teams?search=AI标注组", headers=owner_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "AI标注组"
    assert data["search"] == "AI标注组"


# ============== P08: 搜索 name 子串 ==============

@pytest.mark.asyncio
async def test_p08_search_substring(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """search 子串模糊匹配."""
    await _make_team(db_session, owner, name="AI标注组")
    await _make_team(db_session, owner, name="AI推理组")
    await _make_team(db_session, owner, name="数据清洗组")

    resp = await client.get(
        "/api/teams?search=AI", headers=owner_headers,
    )
    data = resp.json()
    assert data["total"] == 2
    names = {t["name"] for t in data["items"]}
    assert "AI标注组" in names
    assert "AI推理组" in names
    assert "数据清洗组" not in names


# ============== P09: 搜索 description 匹配 ==============

@pytest.mark.asyncio
async def test_p09_search_description(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """search 匹配 description."""
    await _make_team(db_session, owner, name="团队A", description="负责机器学习")
    await _make_team(db_session, owner, name="团队B", description="负责数据标注")

    resp = await client.get(
        "/api/teams?search=机器学习", headers=owner_headers,
    )
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "团队A"


# ============== P10: 搜索 slug 匹配 ==============

@pytest.mark.asyncio
async def test_p10_search_slug(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """search 匹配 slug."""
    await _make_team(db_session, owner, name="Backend团队")  # slug: backend团队
    await _make_team(db_session, owner, name="Frontend团队")

    resp = await client.get(
        "/api/teams?search=backend", headers=owner_headers,
    )
    data = resp.json()
    assert data["total"] == 1
    assert "backend" in data["items"][0]["slug"]


# ============== P11: 搜索 + 分页 ==============

@pytest.mark.asyncio
async def test_p11_search_with_pagination(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """search + page 组合: 12 个 AI 团队, page_size=5 → 3 页."""
    for i in range(12):
        await _make_team(db_session, owner, name=f"AI团队{i:02d}")
    await _make_team(db_session, owner, name="其他团队")

    resp = await client.get(
        "/api/teams?search=AI&page=2&page_size=5",
        headers=owner_headers,
    )
    data = resp.json()
    assert data["total"] == 12
    assert data["page"] == 2
    assert len(data["items"]) == 5
    assert all("AI" in t["name"] for t in data["items"])


# ============== P12: 搜索无结果 ==============

@pytest.mark.asyncio
async def test_p12_search_no_result(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """搜索不存在的关键词 → 空列表."""
    await _make_team(db_session, owner, name="团队A")
    resp = await client.get(
        "/api/teams?search=不存在的关键词xyz", headers=owner_headers,
    )
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["total_pages"] == 0


# ============== P13: 搜索空字符串 ==============

@pytest.mark.asyncio
async def test_p13_search_empty_string(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """search=空字符串 → 视作不搜索, 返回全部."""
    for i in range(3):
        await _make_team(db_session, owner, name=f"团队{i}")
    resp = await client.get(
        "/api/teams?search=", headers=owner_headers,
    )
    data = resp.json()
    assert data["total"] == 3
    assert data["search"] == ""  # 返回原值


# ============== P14: 搜索中文 + 数字 ==============

@pytest.mark.asyncio
async def test_p14_search_chinese_and_digit(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """搜索中文关键词."""
    await _make_team(db_session, owner, name="标注一组")
    await _make_team(db_session, owner, name="标注二组")
    await _make_team(db_session, owner, name="审核组")

    resp = await client.get(
        "/api/teams?search=标注", headers=owner_headers,
    )
    data = resp.json()
    assert data["total"] == 2


# ============== P15: 排序 name_asc ==============

@pytest.mark.asyncio
async def test_p15_sort_name_asc(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """按 name 升序."""
    await _make_team(db_session, owner, name="Zebra组")
    await _make_team(db_session, owner, name="Alpha组")
    await _make_team(db_session, owner, name="Mike组")

    resp = await client.get(
        "/api/teams?sort=name_asc", headers=owner_headers,
    )
    data = resp.json()
    names = [t["name"] for t in data["items"]]
    assert names == ["Alpha组", "Mike组", "Zebra组"]


# ============== P16: 排序 name_desc ==============

@pytest.mark.asyncio
async def test_p16_sort_name_desc(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """按 name 降序."""
    await _make_team(db_session, owner, name="Zebra组")
    await _make_team(db_session, owner, name="Alpha组")
    await _make_team(db_session, owner, name="Mike组")

    resp = await client.get(
        "/api/teams?sort=name_desc", headers=owner_headers,
    )
    data = resp.json()
    names = [t["name"] for t in data["items"]]
    assert names == ["Zebra组", "Mike组", "Alpha组"]


# ============== P17: 排序 member_count_desc ==============

@pytest.mark.asyncio
async def test_p17_sort_member_count_desc(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """按成员数降序."""
    # 大组: 4 成员 (owner + 3 tmp)
    await _make_team(db_session, owner, name="大组", with_members=3)
    # 中组: 2 成员 (owner + 1 tmp)
    await _make_team(db_session, owner, name="中组", with_members=1)
    # 小组: 1 成员 (仅 owner)
    await _make_team(db_session, owner, name="小组")

    resp = await client.get(
        "/api/teams?sort=member_count_desc", headers=owner_headers,
    )
    data = resp.json()
    assert data["items"][0]["name"] == "大组"
    assert data["items"][0]["member_count"] == 4
    assert data["items"][1]["name"] == "中组"
    assert data["items"][1]["member_count"] == 2
    assert data["items"][2]["name"] == "小组"
    assert data["items"][2]["member_count"] == 1


# ============== P18: 排序 created_asc / created_desc ==============

@pytest.mark.asyncio
async def test_p18_sort_created_at(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """按 created_at 排序 (asc / desc)."""
    # 倒序创建, 但 asc 排序后应该按 id 升序
    t1 = await _make_team(db_session, owner, name="最旧")
    t2 = await _make_team(db_session, owner, name="中间")
    t3 = await _make_team(db_session, owner, name="最新")

    # desc: 最新在前
    resp = await client.get(
        "/api/teams?sort=created_desc", headers=owner_headers,
    )
    names_desc = [t["name"] for t in resp.json()["items"]]
    assert names_desc == ["最新", "中间", "最旧"]

    # asc: 最旧在前
    resp = await client.get(
        "/api/teams?sort=created_asc", headers=owner_headers,
    )
    names_asc = [t["name"] for t in resp.json()["items"]]
    assert names_asc == ["最旧", "中间", "最新"]


# ============== P19: 非法 sort 参数 → 422 ==============

@pytest.mark.asyncio
async def test_p19_invalid_sort_param(
    client: AsyncClient,
    owner_headers: dict,
):
    """sort=非法值 → 422."""
    resp = await client.get(
        "/api/teams?sort=hacker_sql", headers=owner_headers,
    )
    assert resp.status_code == 422


# ============== P20: 归档团队不出现在搜索中 ==============

@pytest.mark.asyncio
async def test_p20_archived_excluded_from_search(
    client: AsyncClient,
    owner: User, owner_headers: dict,
    db_session: AsyncSession,
):
    """归档团队不出现在搜索结果 (默认过滤)."""
    t1 = await _make_team(db_session, owner, name="活跃AI组")
    t2 = await _make_team(db_session, owner, name="归档AI组")
    # 归档第二个
    await client.delete(f"/api/teams/{t2}", headers=owner_headers)

    resp = await client.get(
        "/api/teams?search=AI", headers=owner_headers,
    )
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "活跃AI组"
