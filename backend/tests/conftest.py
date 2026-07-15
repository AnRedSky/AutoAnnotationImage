"""
Pytest Configuration & Shared Fixtures
======================================
所有测试共用的 fixtures: 异步客户端、测试用户、测试数据集、临时上传目录
"""
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# 测试环境变量必须在导入 app 之前设置
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ["APP_DEBUG"] = "False"

from app.main import app  # noqa: E402
from app.database import get_db, Base  # noqa: E402
from app.models.user import User  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """每个测试一个全新的内存数据库"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 客户端"""
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """预置一个测试用户"""
    user = User(
        username="testuser",
        email="test@example.com",
        password_hash=hash_password("testpass123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, test_user: User) -> dict:
    """登录后拿到 Bearer token"""
    resp = await client.post(
        "/api/auth/login",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def temp_upload_dir(monkeypatch) -> Generator[Path, None, None]:
    """临时上传目录"""
    tmp = Path(tempfile.mkdtemp(prefix="test_upload_"))
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest_asyncio.fixture
async def test_dataset_factory(client: AsyncClient, auth_headers: dict):
    """工厂 fixture: 异步创建指定数量的数据集 + 类别

    用法:
        await test_dataset_factory(2, name_prefix="ds")
    """
    created_ids = []

    async def _make(count: int = 1, name_prefix: str = "ds",
                    categories: list = None) -> list:
        ids = []
        for i in range(count):
            body = {"name": f"{name_prefix}_{i}", "task_type": "classification"}
            if categories:
                body["category_names"] = categories
            resp = await client.post(
                "/api/datasets", headers=auth_headers, json=body
            )
            assert resp.status_code in (200, 201), f"Create failed: {resp.text}"
            ids.append(resp.json()["id"])
            created_ids.extend(ids)
        return ids

    yield _make
    # cleanup 不强制（测试用 sqlite 内存库）
