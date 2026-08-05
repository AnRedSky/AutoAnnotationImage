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
from app.admin.model.user import User  # noqa: E402
from app.tasks.model.team import Team  # noqa: E402  (v3.3.0)
from app.tasks.model.team_member import TeamMember  # noqa: E402  (v3.3.0)
from app.tasks.model.dataset_membership import DatasetMembership  # noqa: E402
from app.tasks.model.audit_log import AuditLog  # noqa: E402
from app.middleware.security.security import hash_password  # noqa: E402
from app.core.config import settings  # noqa: E402

# 测试模式: 禁用 rate_limit, 避免合跑时 login fixture 触发滑动窗口 429
# (conftest 的 auth_headers fixture 每个测试都 login, 5 次/分钟限制很快触发)
try:
    from app.middleware.http.rate_limit import RATE_LIMIT_PROFILES
    for _profile in RATE_LIMIT_PROFILES.values():
        _profile.enabled = False
except Exception:
    pass  # rate_limit 模块未装也无所谓


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


# v3.5.0: 内存 Redis fixture — control_signals 单元测试不依赖真实 Redis
# 用 dict 模拟 setex/get/delete; 测试间自动重置
#
# 背景: 旧 control_signals.py 测试依赖 127.0.0.1:6379 真实 Redis.
# 在无 Redis 服务或 WS L2 沙箱里跑会超时. 此 fixture 用 in-memory dict 替代,
# 覆盖 setex/get/delete 三个 control_signals 用到的接口, 行为一致.
#
# 实现要点: control_signals.py 的 _redis_or_none() 是从
#   from app.database.redis import redis_client
# 导入的, import 时已捕获引用, 单纯改 app.database.redis.redis_client 不够.
# 必须同时 monkeypatch control_signals 模块的 redis_client 引用 (它在
# 模块内被 _redis_or_none() 读取) 才能生效.
#
# 注意: monkeypatch.setattr 在 fixture 结束后自动还原, 不会污染后续测试.
@pytest.fixture
def fake_redis(monkeypatch):
    """内存版 Redis client — 供 control_signals 等模块在无真实 Redis 时使用

    提供: setex(key, ttl, value), get(key), delete(*keys)
    行为: set/get/delete 同步且符合 Redis 语义
    """
    store = {}  # key -> value (str)

    def _setex(*args, **kwargs):
        # 接受任意签名, 提取 (key, value) 写入 store
        # redis-py 5.x 内部 setex 可能是 self.setex(name, time, value, ...) 4 个 positional
        # (通过 instance 调用, self 自动绑定) 或直接传 3+ args
        # 用 *args 兼容所有情况
        # args 顺序: [self?, key, ttl, value, ...]
        # 我们要的是 key (索引 0 或 1) 和 value
        for i, arg in enumerate(args):
            if isinstance(arg, str) and arg.startswith("train:"):
                # 找到 key (train:xxx)
                key = arg
                # value 应该是下一个 str
                for j in range(i + 1, len(args)):
                    if isinstance(args[j], str) and not args[j].startswith("train:"):
                        store[key] = args[j]
                        return True
        return True

    def _get(*args, **kwargs):
        for arg in args:
            if isinstance(arg, str) and arg.startswith("train:"):
                return store.get(arg)
        return None

    def _delete(*args, **kwargs):
        n = 0
        for k in args:
            if isinstance(k, str) and k in store:
                del store[k]
                n += 1
        return n

    def _set(*args, **kwargs):
        for i, arg in enumerate(args):
            if isinstance(arg, str) and arg.startswith("train:"):
                for j in range(i + 1, len(args)):
                    if isinstance(args[j], str) and not args[j].startswith("train:"):
                        store[arg] = args[j]
                        return True
        return True

    fake = type("FakeRedis", (), {
        "setex": staticmethod(_setex),
        "set": staticmethod(_set),
        "get": staticmethod(_get),
        "delete": staticmethod(_delete),
        "_store": store,
    })()

    # 替换两处:
    # 1) app.database.redis.redis_client — 模块级单例, 影响 _redis_or_none 通过此引用取值
    import app.database.redis as _redis_mod
    monkeypatch.setattr(_redis_mod, "redis_client", fake)
    # 2) app.tasks.workers.control_signals 里 _redis_or_none() 实际读的是它自己的
    #    模块属性 (from import 已绑定到 control_signals.__dict__["redis_client"] 上)
    import app.tasks.workers.control_signals as _cs_mod
    if hasattr(_cs_mod, "redis_client"):
        monkeypatch.setattr(_cs_mod, "redis_client", fake)
    return fake


# v2.5.15 P1-10 / D-3: Celery eager 模式 fixture
# 启用后 .apply_async() / .delay() 立即同步执行 task, 便于单测验证 worker
# 内部逻辑 (DB 写入 / ModelVersion 落盘 等), 不需要启动 worker 进程.
#
# 同时确保 app.database.engine (供 AsyncSessionLocal 使用) 的表结构就绪,
# 否则 worker 任务内的 SQL 操作会报 "no such table".
@pytest_asyncio.fixture
async def celery_eager(db_session):
    """v2.5.15 D-3: Celery eager 模式 + app 引擎建表

    .apply_async() 立即同步执行, 异常向上抛

    注: 复用 db_session 已经在 test engine 上 create_all 的所有表;
       我们需要保证 app.database.engine 也能看到这些表. 由于 conftest 中
       DATABASE_URL 设为 ":memory:" + StaticPool, app 引擎共享同一连接,
       所以这里只需对 app 引擎也跑一次 create_all (幂等).
    """
    from app.tasks.workers.celery_app import celery_app
    from app.database import Base, engine
    import app.admin.model as _admin_model, app.tasks.model as _tasks_model, app.annotation.model as _annotation_model  # noqa: F401  触发 metadata 注册
    import app.tasks.model.team as _team_model  # noqa: F401  (v3.3.0)
    import app.tasks.model.team_member as _team_member_model  # noqa: F401  (v3.3.0)

    # 在 app 引擎上 create_all (幂等)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    orig_eager = celery_app.conf.task_always_eager
    orig_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield celery_app
    finally:
        celery_app.conf.task_always_eager = orig_eager
        celery_app.conf.task_eager_propagates = orig_propagates
