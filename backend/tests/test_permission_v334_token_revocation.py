"""
v3.3.4 P0-3: change_user_role token 吊销测试 (需 fakeredis)
============================================================

覆盖:
  T11: 修改用户角色后, 该用户所有 token 立即失效
  T12: admin 不能自我降级

使用 fakeredis 替换真实 Redis, 不依赖外部服务.
"""
import os
import sys

# 必须在 import app 之前设置 SECRET_KEY (避免 config 启动失败)
os.environ["SECRET_KEY"] = "test-secret-v334-min-32-chars-padding-padding"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, ".")

import fakeredis

# ============== 全局: 用 fakeredis 替换 redis_client ==============
_FAKE_REDIS = fakeredis.FakeRedis(decode_responses=True)

import app.database.redis as _redis_mod
_redis_mod.redis_client = _FAKE_REDIS

import app.middleware.security.token_revocation as _revocation_mod
_revocation_mod.redis_client = _FAKE_REDIS

# ============== import 测试目标 ==============
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.middleware.security.security import (
    hash_password,
    decode_token,
    TokenRevokedError,
)
from app.middleware.security import token_revocation


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """独立内存数据库 (避免与主 conftest 共享)"""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    from app.main import app
    from app.database import get_db
    from httpx import AsyncClient
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def super_admin_user(db_session: AsyncSession) -> User:
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


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"Login {username} failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_role_change_revokes_user_tokens(
    client: AsyncClient, regular_admin_user, super_admin_user
):
    """T11: 修改用户角色后, 该用户所有 token 立即失效

    验证:
      1) admin 登录拿 token
      2) super_admin 调用 change_user_role 改变 admin 角色
      3) admin 的旧 token 立即失效 (decode 抛 TokenRevokedError)
      4) admin 需要重新登录才能继续使用
    """
    # 1) admin 登录拿 token
    admin_headers = await _login(client, "v334_admin", "adminpass334")
    admin_token = admin_headers["Authorization"].replace("Bearer ", "")
    # 验证 admin 旧 token 当前有效
    payload = decode_token(admin_token)
    assert payload["sub"] == str(regular_admin_user.id)

    # 2) super_admin 修改 admin 的角色 (从 admin → viewer)
    super_headers = await _login(client, "v334_super", "superpass123")
    resp = await client.post(
        f"/api/users/{regular_admin_user.id}/role",
        headers=super_headers,
        json={"new_role": "viewer"},
    )
    assert resp.status_code == 200, f"change role failed: {resp.text}"
    assert resp.json()["role"] == "viewer"

    # 3) admin 旧 token 应当失效 (decode 抛 TokenRevokedError)
    with pytest.raises(TokenRevokedError):
        decode_token(admin_token)

    # 4) 通过 get_user_revoked_at 验证时间戳已写入
    revoked_at = token_revocation.get_user_revoked_at(regular_admin_user.id)
    assert revoked_at is not None, "revoke_user should write timestamp to Redis"
    assert token_revocation.is_user_revoked(
        regular_admin_user.id, iat=int(payload["iat"])
    ), "old token iat < revoked_at → should be considered revoked"


@pytest.mark.asyncio
async def test_role_change_self_demote_blocked(
    client: AsyncClient, super_admin_user
):
    """T12: super_admin 不能自我降级为非 admin 角色 (业务保护)"""
    super_headers = await _login(client, "v334_super", "superpass123")
    resp = await client.post(
        f"/api/users/{super_admin_user.id}/role",
        headers=super_headers,
        json={"new_role": "viewer"},
    )
    assert resp.status_code == 400, f"self-demote should be blocked: {resp.text}"


@pytest.mark.asyncio
async def test_role_change_super_to_annotator_allowed_when_not_self(
    client: AsyncClient, db_session, super_admin_user, regular_admin_user
):
    """T12-b: 降低 regular admin 角色 → 不需要被作用方登录态失效之外的额外限制

    验证: admin → annotator 是允许的, 且新 token 立即生效
    """
    # 1) admin 登录拿旧 token
    admin_headers = await _login(client, "v334_admin", "adminpass334")
    admin_old_token = admin_headers["Authorization"].replace("Bearer ", "")
    payload = decode_token(admin_old_token)

    # 2) super 把 admin 降为 annotator
    super_headers = await _login(client, "v334_super", "superpass123")
    resp = await client.post(
        f"/api/users/{regular_admin_user.id}/role",
        headers=super_headers,
        json={"new_role": "annotator"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "annotator"

    # 3) 旧 token 立即失效
    with pytest.raises(TokenRevokedError):
        decode_token(admin_old_token)

    # 4) 重新登录后能正常使用
    # 注: iat 是秒级整数, 如果新登录与吊销在同一秒, iat == revoked_at
    #     → check_revoked 仍会判定为吊销. 需 sleep 跨秒才能确保通过.
    import asyncio
    await asyncio.sleep(1.1)
    new_headers = await _login(client, "v334_admin", "adminpass334")
    resp = await client.get("/api/users/me/profile", headers=new_headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == "annotator"
