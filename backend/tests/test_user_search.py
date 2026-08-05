"""
用户搜索端点测试 (v3.3.1 L2)
==============================

覆盖 GET /api/users/search 端点:
  T01: 普通用户可调用 (不再限制 admin)
  T02: 模糊匹配 username (任意子串)
  T03: 排除自己
  T04: 仅返回 id + username (不暴露 email/role)
  T05: 仅返回 is_active=True 用户
  T06: 长度限制 (q 太短 → 422)
  T07: limit 参数生效
  T08: 空字符串处理
  T09: 未登录调用 → 401
  T10: 中文 / 数字 / 下划线匹配
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.middleware.security.security import hash_password


# ============== Helper Fixtures ==============

@pytest_asyncio.fixture
async def searcher(db_session: AsyncSession) -> User:
    """搜索者 (普通用户, 用于排除自己和验证可见范围)"""
    user = User(
        username="searcher",
        email="searcher@example.com",
        password_hash=hash_password("searcherpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def searcher_headers(client: AsyncClient, searcher: User) -> dict:
    resp = await client.post(
        "/api/auth/login",
        data={"username": "searcher", "password": "searcherpass123"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest_asyncio.fixture
async def seeded_users(db_session: AsyncSession) -> list[User]:
    """预置一批测试用户 (混合 active/inactive, 不同用户名)"""
    users_data = [
        ("alice", "alice@example.com", True),
        ("alicia", "alicia@example.com", True),
        ("bob", "bob@example.com", True),
        ("bobby", "bobby@example.com", True),
        ("carol_2024", "carol@example.com", True),
        ("dave", "dave@example.com", True),
        ("张三", "zhangsan@example.com", True),
        ("disabled_user", "disabled@example.com", False),  # inactive, 不应出现
    ]
    users = []
    for username, email, is_active in users_data:
        user = User(
            username=username,
            email=email,
            password_hash=hash_password("password123"),
            role="annotator",
            is_active=is_active,
        )
        db_session.add(user)
        users.append(user)
    await db_session.commit()
    for u in users:
        await db_session.refresh(u)
    return users


# ============== Tests ==============

@pytest.mark.asyncio
async def test_t01_search_by_annotator_works(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T01: 普通用户 (非 admin) 可调用."""
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "alice"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_t02_search_fuzzy_substring_match(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T02: 模糊匹配 (任意子串) - "ali" 应匹配 alice + alicia."""
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "ali"},
    )
    assert resp.status_code == 200
    usernames = [u["username"] for u in resp.json()["items"]]
    assert "alice" in usernames
    assert "alicia" in usernames
    assert "searcher" not in usernames  # 排除自己


@pytest.mark.asyncio
async def test_t03_excludes_self(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T03: 搜索自己也不应返回 (排除当前用户)."""
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "searcher"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # "searcher" 应该不出现在结果中 (排除自己)
    user_ids = [u["id"] for u in data["items"]]
    assert all(uid != resp.request.headers.get("X-User-Id") for uid in user_ids)
    # 因为只剩自己一个 "searcher" 用户, 排除后应该为空
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_t04_only_returns_id_and_username(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T04: 仅返回 id + username, 不暴露 email/role/is_active."""
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "alice"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) > 0
    for u in items:
        assert "id" in u
        assert "username" in u
        # 敏感字段不应出现
        assert "email" not in u
        assert "role" not in u
        assert "is_active" not in u
        assert "password_hash" not in u


@pytest.mark.asyncio
async def test_t05_excludes_inactive_users(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T05: is_active=False 的用户不应出现在结果中."""
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "disabled"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_t06_q_too_short_returns_422(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T06: q 为空 (min_length=1) → 422."""
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": ""},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_t07_limit_param_caps_results(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T07: limit 参数生效."""
    # 搜 "a" 会匹配多个用户 (alice, alicia, bobby 含 a, dave, 张三含 'a' 不算)
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "a", "limit": 2},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2


@pytest.mark.asyncio
async def test_t08_q_whitespace_only_returns_empty(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T08: q 全空白 → 防御性返回空 (避免 LIKE '%%%' 命中所有用户)."""
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "   "},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_t09_unauthenticated_returns_401(
    client: AsyncClient,
    seeded_users: list[User],
):
    """T09: 未登录 → 401."""
    resp = await client.get(
        "/api/users/search",
        params={"q": "alice"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_t10_chinese_and_digit_username_match(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T10: 中文字符 + 数字用户名 都能正确匹配."""
    # 中文匹配
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "张"},
    )
    assert resp.status_code == 200
    usernames = [u["username"] for u in resp.json()["items"]]
    assert "张三" in usernames

    # 数字匹配
    resp2 = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "2024"},
    )
    assert resp2.status_code == 200
    usernames2 = [u["username"] for u in resp2.json()["items"]]
    assert "carol_2024" in usernames2


@pytest.mark.asyncio
async def test_t11_limit_exceeds_max_returns_422(
    client: AsyncClient,
    searcher_headers: dict,
    seeded_users: list[User],
):
    """T11: limit > 50 → 422 (FastAPI 校验)."""
    resp = await client.get(
        "/api/users/search",
        headers=searcher_headers,
        params={"q": "alice", "limit": 100},
    )
    assert resp.status_code == 422
