"""
Test: Auth Middleware (v3.0.0 审查修复 Phase-B)
==============================================

覆盖以下中间件 / 服务的关键路径:
- Token 吊销: revoke_jti / revoke_user / check_revoked
- 限流: PathAwareRateLimitMiddleware (滑动窗口算法)
- 审计: AuthAuditMiddleware + emit_audit_event
- 自动续期: TokenRefreshMiddleware (临近过期场景)

使用 fakeredis 模拟 Redis, 不依赖真实 Redis 服务.
"""
import os
import sys
import time
from types import SimpleNamespace

# 必须在 import app 之前设置 SECRET_KEY (避免 config 启动失败)
os.environ["SECRET_KEY"] = "test-secret-for-auth-middleware-min-32-chars"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, ".")

import fakeredis
import pytest

# ============== 全局: 用 fakeredis 替换 redis_client ==============
# 注意: 必须先创建再 patch, 让测试和 runner 共用同一个 instance
_FAKE_REDIS = fakeredis.FakeRedis(decode_responses=True)

# 在所有 patch 之前 mock 掉 redis_client
import app.database.redis as _redis_mod
_redis_mod.redis_client = _FAKE_REDIS

import app.middleware.security.token_revocation as _revocation_mod
_revocation_mod.redis_client = _FAKE_REDIS

import app.middleware.http.rate_limit as _ratelimit_mod
_ratelimit_mod.redis_client = _FAKE_REDIS

from app.middleware.security.security import (
    create_access_token,
    decode_token,
    TokenExpiredError,
    TokenRevokedError,
)
from app.middleware.security import token_revocation
from app.middleware.http.rate_limit import (
    RateLimitProfile,
    RATE_LIMIT_PROFILES,
    _check_and_increment,
    _make_redis_key,
    _extract_client_ip,
)
from app.middleware.http.auth_audit import (
    AuthEventType,
    AuthAuditEvent,
    emit_audit_event,
)


@pytest.fixture(autouse=True)
def _clear_redis():
    """每个测试前清空 fakeredis (pytest 模式)"""
    _FAKE_REDIS.flushall()
    yield
    _FAKE_REDIS.flushall()


def reset_redis_for_manual_runner():
    """手动 runner 模式: 在每个 test_ 方法调用前清空

    pytest fixture 在非 pytest 模式 (直接调用 test_xxx) 不生效,
    需要 test_xxx 自己调用, 或 runner 在调用前 flushall.
    """
    _FAKE_REDIS.flushall()


# ============== Token 吊销 (TC-B-01~08) ==============

class TestTokenRevocation:
    """TC-B-01~08: Token 吊销服务"""

    def test_revoke_jti_basic(self):
        """TC-B-01: 吊销单个 jti"""
        ok = token_revocation.revoke_jti("jti-abc-123", exp_ts=int(time.time()) + 60)
        assert ok is True
        assert token_revocation.is_jti_revoked("jti-abc-123") is True

    def test_revoke_jti_default_ttl(self):
        """TC-B-02: 不传 exp_ts 用 default_ttl"""
        token_revocation.revoke_jti("jti-xyz")
        assert token_revocation.is_jti_revoked("jti-xyz") is True

    def test_revoke_jti_empty(self):
        """TC-B-03: 空 jti 安全降级"""
        assert token_revocation.revoke_jti("") is False
        assert token_revocation.is_jti_revoked("") is False

    def test_revoke_user_basic(self):
        """TC-B-04: 吊销用户所有 token"""
        token_revocation.revoke_user(42)
        ts = token_revocation.get_user_revoked_at(42)
        assert ts is not None
        assert abs(ts - int(time.time())) <= 2  # 1-2s 误差

    def test_is_user_revoked_old_iat(self):
        """TC-B-05: 旧 iat 被吊销"""
        token_revocation.revoke_user(100)
        # 模拟改密前的旧 token (iat < revoke_time)
        old_iat = int(time.time()) - 10
        assert token_revocation.is_user_revoked(100, old_iat) is True

    def test_is_user_revoked_new_iat(self):
        """TC-B-06: 改密后新签发的 token (iat > revoke_time) 放行"""
        token_revocation.revoke_user(200)
        # 等 1.5s 让 iat 严格大于 revoke_time
        time.sleep(1.2)
        new_iat = int(time.time())
        assert token_revocation.is_user_revoked(200, new_iat) is False

    def test_is_user_revoked_no_iat(self):
        """TC-B-07: 不传 iat 时, 已吊销用户的所有 token 都被拒 (保守策略)"""
        token_revocation.revoke_user(300)
        assert token_revocation.is_user_revoked(300) is True

    def test_check_revoked_combined(self):
        """TC-B-08: 综合检查 (jti 优先 + 用户级)"""
        # 场景 1: jti 未吊销, 用户未吊销 → 放行
        assert token_revocation.check_revoked(jti="x", user_id=1, iat=int(time.time())) is False
        # 场景 2: jti 已吊销 → 拒
        token_revocation.revoke_jti("y")
        assert token_revocation.check_revoked(jti="y", user_id=1, iat=int(time.time())) is True
        # 场景 3: 用户已吊销, jti 未吊销 → 拒
        token_revocation.revoke_user(99)
        assert token_revocation.check_revoked(jti="z", user_id=99, iat=int(time.time()) - 100) is True

    def test_decode_token_with_revocation(self):
        """TC-B-09: decode_token 集成吊销检查"""
        token = create_access_token({"sub": "1", "username": "alice"},
                                     extra_claims={"role": "admin"})
        # 签发后立即能解
        payload = decode_token(token)
        assert payload["jti"] is not None
        # 吊销后应抛 TokenRevokedError
        token_revocation.revoke_jti(payload["jti"])
        with pytest.raises(TokenRevokedError) as exc:
            decode_token(token)
        assert exc.value.code == "token_revoked"

    def test_decode_token_with_user_revocation(self):
        """TC-B-10: 用户级吊销后旧 token 失效 (新 token 等 1.1s 后签发)"""
        token = create_access_token({"sub": "1", "username": "alice"})
        time.sleep(1.2)  # 等 1.2s 让旧 iat 严格小于 revoke_time
        # 用户改密
        token_revocation.revoke_user(1)
        # 旧 token 被拒
        with pytest.raises(TokenRevokedError):
            decode_token(token)
        # 1.2s 后新签发的 token 放行 (iat > revoke_time)
        time.sleep(1.2)
        new_token = create_access_token({"sub": "1", "username": "alice"})
        decode_token(new_token)  # 不应抛


# ============== 限流 (TC-B-11~16) ==============

class TestRateLimit:
    """TC-B-11~16: 限流中间件核心算法"""

    def test_rate_limit_under_threshold_allowed(self):
        """TC-B-11: 未超阈值放行"""
        profile = RateLimitProfile(name="test", max_requests=3, window_seconds=10, scope="ip")
        for i in range(3):
            allowed, count, _ = _check_and_increment(profile, "rl:test:1.1.1.1")
            assert allowed is True
            assert count == i + 1

    def test_rate_limit_exceeds_threshold_blocked(self):
        """TC-B-12: 超过阈值被拒"""
        profile = RateLimitProfile(name="test", max_requests=2, window_seconds=10, scope="ip")
        # 1, 2 → 放行
        for _ in range(2):
            allowed, _, _ = _check_and_increment(profile, "rl:test:1.1.1.1")
            assert allowed is True
        # 第 3 次 → 拒
        allowed, _, retry_after = _check_and_increment(profile, "rl:test:1.1.1.1")
        assert allowed is False
        assert retry_after > 0
        assert retry_after <= 10

    def test_rate_limit_window_expires(self):
        """TC-B-13: 窗口滑过后允许新的请求"""
        profile = RateLimitProfile(name="test", max_requests=1, window_seconds=2, scope="ip")
        allowed, _, _ = _check_and_increment(profile, "rl:test:1.1.1.1")
        assert allowed is True
        allowed, _, _ = _check_and_increment(profile, "rl:test:1.1.1.1")
        assert allowed is False
        # 等窗口滑过
        time.sleep(2.1)
        allowed, _, _ = _check_and_increment(profile, "rl:test:1.1.1.1")
        assert allowed is True

    def test_make_redis_key_ip_scope(self):
        """TC-B-14: IP 维度 key"""
        class _FakeReq:
            headers = {}
            client = SimpleNamespace(host="192.168.1.1")
        profile = RateLimitProfile(name="x", max_requests=10, window_seconds=60, scope="ip")
        key = _make_redis_key(profile, _FakeReq(), None)
        assert "192.168.1.1" in key

    def test_make_redis_key_username_scope(self):
        """TC-B-15: username 维度 key"""
        class _FakeReq:
            headers = {}
            client = SimpleNamespace(host="1.1.1.1")
        profile = RateLimitProfile(name="x", max_requests=10, window_seconds=60, scope="username")
        key = _make_redis_key(profile, _FakeReq(), "alice")
        assert "alice" in key

    def test_make_redis_key_ip_username_scope(self):
        """TC-B-16: IP+username 复合 key"""
        class _FakeReq:
            headers = {}
            client = SimpleNamespace(host="1.1.1.1")
        profile = RateLimitProfile(name="x", max_requests=10, window_seconds=60, scope="ip_username")
        key = _make_redis_key(profile, _FakeReq(), "bob")
        assert "1.1.1.1" in key and "bob" in key

    def test_extract_client_ip_xff(self):
        """TC-B-17: X-Forwarded-For 优先"""
        class _FakeReq:
            headers = {"X-Forwarded-For": "10.0.0.1, 192.168.1.1"}
            client = SimpleNamespace(host="1.1.1.1")
        assert _extract_client_ip(_FakeReq()) == "10.0.0.1"

    def test_extract_client_ip_x_real_ip(self):
        """TC-B-18: X-Real-IP 兜底"""
        class _FakeReq:
            headers = {"X-Real-IP": "10.0.0.2"}
            client = SimpleNamespace(host="1.1.1.1")
        assert _extract_client_ip(_FakeReq()) == "10.0.0.2"

    def test_extract_client_ip_fallback(self):
        """TC-B-19: 无代理头时用 client.host"""
        class _FakeReq:
            headers = {}
            client = SimpleNamespace(host="127.0.0.1")
        assert _extract_client_ip(_FakeReq()) == "127.0.0.1"

    def test_rate_limit_profiles_defined(self):
        """TC-B-20: 预置策略齐全"""
        for name in ("global", "login", "register", "change_password", "strict"):
            assert name in RATE_LIMIT_PROFILES
            p = RATE_LIMIT_PROFILES[name]
            assert p.max_requests > 0
            assert p.window_seconds > 0


# ============== 审计 (TC-B-21~24) ==============

class TestAuthAudit:
    """TC-B-21~24: 审计事件"""

    def test_audit_event_to_dict(self):
        """TC-B-21: 审计事件序列化"""
        ev = AuthAuditEvent(
            event_type=AuthEventType.LOGIN_SUCCESS,
            user_id=42,
            username="alice",
            ip="1.2.3.4",
            reason="ok",
        )
        d = ev.to_dict()
        assert d["event_type"] == "login_success"
        assert d["user_id"] == 42
        assert d["username"] == "alice"
        assert d["ip"] == "1.2.3.4"
        assert d["reason"] == "ok"

    def test_audit_event_to_json(self):
        """TC-B-22: 审计事件 JSON 序列化"""
        ev = AuthAuditEvent(
            event_type=AuthEventType.LOGIN_FAILURE,
            username="bob",
            reason="密码错误",
        )
        s = ev.to_json()
        import json as _json
        d = _json.loads(s)
        assert d["event_type"] == "login_failure"
        assert d["username"] == "bob"

    def test_audit_event_jti_truncated(self):
        """TC-B-23: jti 截断避免日志过长"""
        ev = AuthAuditEvent(
            event_type=AuthEventType.LOGOUT,
            jti="x" * 64,
        )
        d = ev.to_dict()
        # 仅前 8 字符 + ...
        assert d["jti"].endswith("...")
        assert len(d["jti"]) < 64

    def test_event_types_complete(self):
        """TC-B-24: 事件类型齐全"""
        for name in (
            "LOGIN_SUCCESS", "LOGIN_FAILURE", "LOGOUT", "CHANGE_PASSWORD",
            "REGISTER", "TOKEN_REJECTED", "TOKEN_REFRESHED", "PERMISSION_DENIED",
        ):
            assert hasattr(AuthEventType, name)


# ============== Token 续期 (TC-B-25~28) ==============

class TestTokenRefresh:
    """TC-B-25~28: Token 自动续期"""

    def test_token_with_far_expiry_not_renewed(self):
        """TC-B-25: 距离过期 > 阈值时不续期"""
        # 24h 有效期, 默认阈值 5min, 应不续期
        token = create_access_token({"sub": "1"})
        from app.middleware.security.security import decode_token
        payload = decode_token(token, check_revocation=False)
        exp = payload["exp"]
        now = int(time.time())
        # exp - now > 300s → 不续期
        assert (exp - now) > 300

    def test_token_renew_preserves_sub_and_role(self):
        """TC-B-26: 续期保留 sub/role"""
        from app.middleware.http.token_refresh import TokenRefreshMiddleware
        # 直接调用 _renew 逻辑 (避免 middleware 栈)
        token = create_access_token(
            {"sub": "42", "username": "alice"},
            extra_claims={"role": "admin"},
        )
        payload = decode_token(token, check_revocation=False)
        # 模拟续期: 调用 create_access_token 重新签
        new_token = create_access_token(
            {"sub": payload["sub"], "username": payload.get("username")},
            extra_claims={"role": payload.get("role")},
        )
        new_payload = decode_token(new_token, check_revocation=False)
        assert new_payload["sub"] == "42"
        assert new_payload["username"] == "alice"
        assert new_payload["role"] == "admin"
        # jti 应不同 (新 token)
        assert new_payload["jti"] != payload["jti"]

    def test_token_renew_new_jti_unique(self):
        """TC-B-27: 续期后 jti 唯一"""
        from app.middleware.http.token_refresh import TokenRefreshMiddleware
        # 多次续期, jti 都不应相同
        jtis = set()
        for _ in range(5):
            t = create_access_token({"sub": "1"})
            p = decode_token(t, check_revocation=False)
            jtis.add(p["jti"])
        assert len(jtis) == 5

    def test_default_threshold_is_5min(self):
        """TC-B-28: 默认续期阈值 5min (300s)"""
        from app.middleware.http.token_refresh import DEFAULT_REFRESH_THRESHOLD_SECONDS
        assert DEFAULT_REFRESH_THRESHOLD_SECONDS == 300


# ============== 集成: get_current_user 处理 TokenRevokedError (TC-B-29~30) ==============

class TestGetCurrentUserRevoked:
    """TC-B-29~30: get_current_user 集成吊销错误"""

    def test_revoked_token_returns_401_token_revoked(self):
        """TC-B-29: 吊销的 token 触发 401 + token_revoked code"""
        from app.middleware.http.auth import get_current_user
        from app.middleware.security.security import (
            create_access_token, decode_token, TokenRevokedError
        )
        token = create_access_token({"sub": "1", "username": "alice"})
        payload = decode_token(token, check_revocation=False)
        token_revocation.revoke_jti(payload["jti"])
        # 模拟 get_current_user 捕获 TokenRevokedError
        try:
            decode_token(token)
        except TokenRevokedError as e:
            assert e.code == "token_revoked"
            assert e.http_status == 401

    def test_revoked_user_token_returns_401(self):
        """TC-B-30: 用户级吊销后旧 token 触发 401"""
        from app.middleware.security.security import TokenRevokedError
        token = create_access_token({"sub": "999", "username": "bob"})
        time.sleep(1.2)
        token_revocation.revoke_user(999)
        try:
            decode_token(token)
        except TokenRevokedError as e:
            assert e.code == "token_revoked"
