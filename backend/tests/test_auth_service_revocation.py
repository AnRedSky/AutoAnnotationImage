"""
Test: AuthService 集成 Token 吊销 (v3.0.0 审查修复 Phase-B)
==========================================================

覆盖 auth_service.login / register / change_password / logout 与:
- token_revocation 集成 (change_password 吊销全部, logout 吊销当前 jti)
- auth_audit.emit_audit_event 调用 (login_success / login_failure / change_password / logout / register)

使用 fakeredis 模拟 Redis, 不依赖真实 Redis / MySQL / 业务路由.
"""
import os
import sys
import time
from unittest.mock import MagicMock, AsyncMock

# 必须在 import app 之前设置 SECRET_KEY (避免 config 启动失败)
os.environ["SECRET_KEY"] = "test-secret-for-auth-service-revocation-min-32-chars"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, ".")

import fakeredis

# ============== 全局: 用 fakeredis 替换 redis_client ==============
_FAKE_REDIS = fakeredis.FakeRedis(decode_responses=True)

import app.database.redis as _redis_mod
_redis_mod.redis_client = _FAKE_REDIS

import app.middleware.security.token_revocation as _revocation_mod
_revocation_mod.redis_client = _FAKE_REDIS

import app.middleware.http.rate_limit as _ratelimit_mod
_ratelimit_mod.redis_client = _FAKE_REDIS

import app.middleware.http.auth_audit as _audit_mod
_audit_mod.redis_client = _FAKE_REDIS

# ============== spy 替换 emit_audit_event (审计事件) ==============
_captured_events: list = []


def _spy_emit(event_type, **kwargs):
    """spy 版本的 emit_audit_event, 只记录不写日志"""
    _captured_events.append({"type": event_type, **kwargs})
    return None


# 关键: 先 patch 源头, 再 reload service, 这样 service 引用的就是 spy
_audit_mod.emit_audit_event = _spy_emit
# 同步覆盖 service 模块内已 import 的引用
import app.auth.service.auth_service as _auth_svc_mod
_auth_svc_mod.emit_audit_event = _spy_emit

# ============== import 测试目标 (auth_service 的核心) ==============
from app.middleware.security import token_revocation
from app.middleware.security.security import (
    create_access_token,
    decode_token,
    TokenRevokedError,
    get_password_hash,
    verify_password,
)
from fastapi import HTTPException


def _make_user(id_: int = 1, username: str = "alice", password: str = "old-pass-123",
               role: str = "annotator", is_active: bool = True) -> MagicMock:
    """构造一个 mock User, 不依赖 DB"""
    user = MagicMock()
    user.id = id_
    user.username = username
    user.password_hash = get_password_hash(password)
    user.role = role
    user.is_active = is_active
    user.email = f"{username}@test.local"
    return user


def _make_async_db() -> AsyncMock:
    """mock async db (commit/refresh 是 await 的)"""
    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


def _run_async(coro):
    """运行异步协程 (无 pytest-asyncio 时用)"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在 pytest 中, 用 nest_asyncio 或新 loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ============== Fixture: 清理 ==============

def _reset_state():
    _FAKE_REDIS.flushall()
    _captured_events.clear()


# ============== TC-ASR-01: change_password 成功 → revoke_user ==============

class TestChangePasswordRevokesAllTokens:
    """TC-ASR-01~04: change_password 必须吊销该用户全部 token"""

    def test_change_password_revoke_user_called(self):
        """TC-ASR-01: 改密成功后, 旧 token 被拒"""
        _reset_state()
        user = _make_user(id_=1, password="old-pass-123")
        old_token = create_access_token({"sub": "1", "username": "alice"})

        db = _make_async_db()
        result = _run_async(_auth_svc_mod.AuthService.change_password(
            db, user, "old-pass-123", "new-pass-456",
        ))
        # 断言: 密码已更新
        assert verify_password("new-pass-456", result.password_hash)

        # 断言: 用户被吊销 (旧 token 应被拒)
        time.sleep(1.2)
        try:
            decode_token(old_token)
            raise AssertionError("old_token should be revoked")
        except TokenRevokedError:
            pass  # 预期

    def test_change_password_emits_audit_event(self):
        """TC-ASR-02: 改密成功 emit CHANGE_PASSWORD 事件"""
        _reset_state()
        user = _make_user(id_=2, password="p1")
        db = _make_async_db()
        _run_async(_auth_svc_mod.AuthService.change_password(db, user, "p1", "p2-new"))

        change_events = [e for e in _captured_events if e["type"] == "change_password"]
        assert len(change_events) == 1
        assert change_events[0]["user_id"] == 2
        assert change_events[0]["username"] == "alice"

    def test_change_password_wrong_old_password(self):
        """TC-ASR-03: 旧密码错 → 不吊销 token, 抛 401, emit 失败事件"""
        _reset_state()
        user = _make_user(id_=3, password="correct")
        token = create_access_token({"sub": "3"})

        db = _make_async_db()
        try:
            _run_async(_auth_svc_mod.AuthService.change_password(db, user, "wrong-old", "new"))
            raise AssertionError("should raise HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 401

        # 旧 token 仍可用 (未吊销)
        decode_token(token)  # 不应抛

        # 审计: 记录了失败事件
        fail_events = [e for e in _captured_events if e["type"] == "change_password"]
        assert len(fail_events) == 1
        assert "旧密码错误" in fail_events[0]["reason"]


# ============== TC-ASR-04: logout 吊销当前 jti ==============

class TestLogoutRevokesJti:
    """TC-ASR-04~06: logout 吊销当前 token 的 jti"""

    def test_logout_revoke_jti(self):
        """TC-ASR-04: 传 jti+exp → 写入黑名单, 后续 decode 失败"""
        _reset_state()
        user = _make_user(id_=4)
        token = create_access_token({"sub": "4", "username": "alice"})
        payload = decode_token(token, check_revocation=False)
        jti = payload["jti"]
        exp = payload["exp"]

        result = _auth_svc_mod.AuthService.logout(user, jti=jti, exp_ts=exp)
        assert result["jti_revoked"] is True
        assert result["user_id"] == 4

        # 该 jti 已被吊销, decode 应抛 TokenRevokedError
        try:
            decode_token(token)
            raise AssertionError("token should be revoked")
        except TokenRevokedError:
            pass

    def test_logout_emits_audit_event(self):
        """TC-ASR-05: logout emit LOGOUT 事件"""
        _reset_state()
        user = _make_user(id_=5)
        _auth_svc_mod.AuthService.logout(user, jti="test-jti-xyz", exp_ts=int(time.time()) + 3600)
        logout_events = [e for e in _captured_events if e["type"] == "logout"]
        assert len(logout_events) == 1
        assert logout_events[0]["user_id"] == 5
        assert logout_events[0]["jti"] == "test-jti-xyz"

    def test_logout_without_jti_emit_audit_but_no_revoke(self):
        """TC-ASR-06: 不传 jti (兜底) → 仍 emit 事件, 不抛错"""
        _reset_state()
        user = _make_user(id_=6)
        result = _auth_svc_mod.AuthService.logout(user, jti=None, exp_ts=None)
        assert result["jti_revoked"] is False
        # 审计事件仍触发
        logout_events = [e for e in _captured_events if e["type"] == "logout"]
        assert len(logout_events) == 1


# ============== TC-ASR-07: login 审计事件 ==============

class TestLoginAuditEvents:
    """TC-ASR-07~09: login 成功/失败都 emit 审计"""

    def test_login_success_emit_audit(self):
        """TC-ASR-07: 登录成功 → emit LOGIN_SUCCESS"""
        _reset_state()
        user = _make_user(id_=7, password="p1")

        async def _fake_get_user(db, username):
            return user if username == "alice" else None
        _auth_svc_mod.get_user_by_username = _fake_get_user

        db = _make_async_db()
        result = _run_async(_auth_svc_mod.AuthService.login(db, "alice", "p1"))
        assert "access_token" in result
        assert result["user"]["id"] == 7

        events = [e for e in _captured_events if e["type"] == "login_success"]
        assert len(events) == 1
        assert events[0]["user_id"] == 7

    def test_login_failure_emit_audit(self):
        """TC-ASR-08: 登录失败 (密码错) → emit LOGIN_FAILURE"""
        _reset_state()
        user = _make_user(id_=8, password="correct")

        async def _fake(db, username):
            return user if username == "alice" else None
        _auth_svc_mod.get_user_by_username = _fake

        db = _make_async_db()
        try:
            _run_async(_auth_svc_mod.AuthService.login(db, "alice", "wrong"))
            raise AssertionError("should raise")
        except HTTPException as exc:
            assert exc.status_code == 401

        events = [e for e in _captured_events if e["type"] == "login_failure"]
        assert len(events) == 1
        assert events[0]["username"] == "alice"

    def test_login_inactive_user_emit_audit(self):
        """TC-ASR-09: 账号停用 → emit LOGIN_FAILURE (403)"""
        _reset_state()
        user = _make_user(id_=9, password="p1", is_active=False)

        async def _fake(db, username):
            return user if username == "bob" else None
        _auth_svc_mod.get_user_by_username = _fake

        db = _make_async_db()
        try:
            _run_async(_auth_svc_mod.AuthService.login(db, "bob", "p1"))
            raise AssertionError("should raise")
        except HTTPException as exc:
            assert exc.status_code == 403

        events = [e for e in _captured_events if e["type"] == "login_failure"]
        assert len(events) == 1
        assert "停用" in events[0]["reason"]


# ============== TC-ASR-10: 集成: 改密后旧 token 不能再用, 新 token 可以 ==============

class TestIntegration:
    """端到端集成: 改密 → 旧 token 失效 → 新 token 生效"""

    def test_change_password_full_lifecycle(self):
        """TC-ASR-10: 完整生命周期"""
        _reset_state()
        user = _make_user(id_=100, password="initial-pass")
        db = _make_async_db()

        # 1. 签发初始 token
        old_token = create_access_token({"sub": "100"})

        # 2. 改密
        _run_async(_auth_svc_mod.AuthService.change_password(
            db, user, "initial-pass", "rotated-pass",
        ))

        # 3. 旧 token 立即失效
        time.sleep(1.2)
        try:
            decode_token(old_token)
            raise AssertionError("old should be revoked")
        except TokenRevokedError:
            pass

        # 4. 1.2s 后签发新 token, 应能正常 decode
        time.sleep(1.2)
        new_token = create_access_token({"sub": "100"})
        payload = decode_token(new_token)  # 不抛
        assert payload["sub"] == "100"

        # 5. 密码哈希确实更新了
        assert verify_password("rotated-pass", user.password_hash)
        assert not verify_password("initial-pass", user.password_hash)


# ============== TC-ASR-11: get_current_user_with_payload 单测 ==============

class TestGetCurrentUserWithPayload:
    """TC-ASR-11~12: 新依赖 get_current_user_with_payload 行为正确"""

    def test_dependency_returns_named_tuple(self):
        """TC-ASR-11: 依赖返回 NamedTuple, 可解构"""
        from app.middleware.http.auth import (
            get_current_user_with_payload,
            CurrentUserContext,
        )
        assert CurrentUserContext._fields == ("user", "payload", "raw_token")

    def test_dependency_revoke_via_logout_e2e(self):
        """TC-ASR-12: 模拟完整流程 - 登录拿 token → logout 吊销 → 后续 401"""
        _reset_state()
        user = _make_user(id_=200)
        token = create_access_token({"sub": "200", "username": "alice"})
        payload = decode_token(token, check_revocation=False)

        # 模拟 endpoint: 拿 ctx, 调 logout
        jti = payload.get("jti")
        exp = payload.get("exp")
        result = _auth_svc_mod.AuthService.logout(user, jti=jti, exp_ts=exp)
        assert result["jti_revoked"] is True

        # 后续任何请求用此 token 都会被拒
        try:
            decode_token(token)
            raise AssertionError("should be revoked")
        except TokenRevokedError:
            pass


# ============== TC-ASR-13: register 审计 ==============

class TestRegisterAuditEvents:
    """TC-ASR-13: register 成功/失败都 emit 审计"""

    def test_register_success_emit_audit(self):
        """TC-ASR-13: 注册成功 → emit REGISTER"""
        _reset_state()

        async def _fake(db, username):
            return None  # 用户名不存在
        _auth_svc_mod.get_user_by_username = _fake

        db = _make_async_db()

        # 自定义一个简单类, 避免 SQLAlchemy relationship 解析
        class _StubUser:
            id = 999
            username = "newuser"
            role = "annotator"
            email = "new@test.local"
            password_hash = "stub"

        captured_user = {}

        def _add(user):
            user.id = 999
            captured_user["u"] = user

        async def _refresh(user):
            pass
        db.add.side_effect = _add
        db.refresh.side_effect = _refresh

        # mock User 构造, 返回 stub 实例
        from app.admin.model import user as _user_mod
        _orig_user_cls = _user_mod.User

        def _fake_user_ctor(**kwargs):
            u = _StubUser()
            u.__dict__.update(kwargs)
            return u

        # patch in service module
        _auth_svc_mod.User = _fake_user_ctor

        try:
            user = _run_async(_auth_svc_mod.AuthService.register(
                db, "newuser", "pass123", "new@test.local", "annotator",
            ))
            assert user.id == 999
        finally:
            _auth_svc_mod.User = _orig_user_cls

        events = [e for e in _captured_events if e["type"] == "register"]
        assert len(events) == 1
        assert events[0]["user_id"] == 999

    def test_register_duplicate_emit_audit(self):
        """TC-ASR-14: 用户名重复 → emit REGISTER (reason=已存在)"""
        _reset_state()
        existing = _make_user(id_=10, username="dup")

        async def _fake(db, username):
            return existing if username == "dup" else None
        _auth_svc_mod.get_user_by_username = _fake

        db = _make_async_db()
        try:
            _run_async(_auth_svc_mod.AuthService.register(db, "dup", "pass123"))
            raise AssertionError("should raise")
        except HTTPException as exc:
            assert exc.status_code == 409

        events = [e for e in _captured_events if e["type"] == "register"]
        assert len(events) == 1
        assert "已存在" in events[0]["reason"]


# ============== Test Runner (无 pytest 时也可直接 python 运行) ==============

def _run_all_tests():
    """手动运行所有测试 (用于 conftest 路径过期时)"""
    import inspect
    cls_methods = []
    for name, obj in list(globals().items()):
        if inspect.isclass(obj) and name.startswith("Test"):
            for m in inspect.getmembers(obj, predicate=inspect.isfunction):
                if m[0].startswith("test_"):
                    cls_methods.append((name, m[0]))

    passed, failed = 0, 0
    failed_list = []
    for cls_name, method_name in cls_methods:
        cls = globals()[cls_name]
        inst = cls()
        try:
            getattr(inst, method_name)()
            print(f"  ✓ {cls_name}.{method_name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {cls_name}.{method_name}: {type(e).__name__}: {e}")
            failed += 1
            failed_list.append((cls_name, method_name, e))

    print(f"\nTotal: {passed + failed} | Passed: {passed} | Failed: {failed}")
    return failed == 0


if __name__ == "__main__":
    _run_all_tests()
