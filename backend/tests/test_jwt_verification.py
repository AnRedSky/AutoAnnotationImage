"""
Test: JWT Verification (v3.0.0 审查修复 Phase-A)
================================================

覆盖 create_access_token / decode_token 的所有关键路径:
- 标准化 claims (sub/iat/exp/jti/iss/aud/role)
- 异常细分 (Expired/Signature/Claims/Malformed/MissingClaim)
- iss/aud 校验失败
- 时钟漂移容忍 (leeway)
- payload 篡改检测
- AuthService.issue_token 集成
- get_current_user 异常路径
"""
import time
import pytest
from jose import jwt as jose_jwt

from app.middleware.security.security import (
    create_access_token,
    decode_token,
    decode_token_safe,
    TokenValidationError,
    TokenExpiredError,
    TokenSignatureError,
    TokenClaimsError,
    TokenMalformedError,
    TokenMissingClaimError,
)
from app.core.config import settings


# ============== 标准化 claims ==============

class TestCreateAccessToken:
    """TC-JWT-01~05: create_access_token 标准化 claims"""

    def test_injects_all_required_claims(self):
        """TC-JWT-01: 自动注入 sub/iat/exp/jti/iss/aud"""
        token = create_access_token({"sub": "42"})
        payload = decode_token(token)
        for claim in ("sub", "iat", "exp", "jti", "iss", "aud"):
            assert claim in payload, f"missing claim: {claim}"
        assert payload["sub"] == "42"  # 强制转 str
        assert payload["iss"] == settings.JWT_ISSUER
        assert payload["aud"] == settings.JWT_AUDIENCE

    def test_jti_is_unique(self):
        """TC-JWT-02: 每次签发 jti 唯一"""
        t1 = create_access_token({"sub": "1"})
        t2 = create_access_token({"sub": "1"})
        p1 = decode_token(t1)
        p2 = decode_token(t2)
        assert p1["jti"] != p2["jti"]
        assert len(p1["jti"]) == 32  # secrets.token_hex(16) → 32 字符

    def test_extra_claims_merged(self):
        """TC-JWT-03: 业务字段 (role) 通过 extra_claims 注入"""
        token = create_access_token(
            {"sub": "1", "username": "alice"},
            extra_claims={"role": "admin", "department": "ml"},
        )
        payload = decode_token(token)
        assert payload["role"] == "admin"
        assert payload["department"] == "ml"
        assert payload["username"] == "alice"

    def test_extra_claims_cannot_override_required(self):
        """TC-JWT-04: extra_claims 中的保留声明被跳过, 防止伪造"""
        token = create_access_token(
            {"sub": "1"},
            extra_claims={"iss": "evil-issuer", "aud": "evil-aud", "role": "admin"},
        )
        payload = decode_token(token)
        # iss/aud 应被标准值覆盖, 不能被业务侧注入
        assert payload["iss"] == settings.JWT_ISSUER
        assert payload["aud"] == settings.JWT_AUDIENCE
        assert payload["role"] == "admin"  # 业务字段照常通过

    def test_custom_jti_respected(self):
        """TC-JWT-05: 调用方可显式指定 jti"""
        token = create_access_token({"sub": "1"}, jti="my-custom-jti-12345")
        payload = decode_token(token)
        assert payload["jti"] == "my-custom-jti-12345"

    def test_int_sub_converted_to_str(self):
        """TC-JWT-06: 整数 sub 自动转字符串 (RFC 7519)"""
        token = create_access_token({"sub": 123})
        payload = decode_token(token)
        assert payload["sub"] == "123"
        assert isinstance(payload["sub"], str)

    def test_expires_minutes_override(self):
        """TC-JWT-07: 自定义 expires_minutes"""
        token = create_access_token({"sub": "1"}, expires_minutes=5)
        payload = decode_token(token)
        delta = payload["exp"] - payload["iat"]
        assert 295 <= delta <= 305  # 5min ± 5s


# ============== decode_token 异常细分 ==============

class TestDecodeTokenErrors:
    """TC-JWT-08~15: decode_token 抛细分异常"""

    def test_expired_token_raises_expired(self):
        """TC-JWT-08: 已过期 token 抛 TokenExpiredError (关闭 leeway)"""
        # 手工造一个明显已过期的 token (300s 前), 关闭 leeway 避免被时钟漂移容忍
        now = int(time.time())
        bad_token = jose_jwt.encode(
            {
                "sub": "1",
                "iat": now - 300,
                "exp": now - 300,  # 300s 前过期
                "jti": "x",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
            },
            settings.EFFECTIVE_JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(TokenExpiredError) as exc_info:
            decode_token(bad_token, leeway=0)
        assert exc_info.value.code == "token_expired"
        assert exc_info.value.http_status == 401

    def test_wrong_signature_raises_signature_error(self):
        """TC-JWT-09: 签名不匹配抛 TokenSignatureError"""
        # 用别的 secret 签一个 token
        bad_token = jose_jwt.encode(
            {
                "sub": "1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "jti": "fake",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
            },
            "wrong-secret-key-1234567890",
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(TokenSignatureError) as exc_info:
            decode_token(bad_token)
        assert exc_info.value.code == "token_signature_invalid"

    def test_wrong_issuer_raises_claims_error(self):
        """TC-JWT-10: iss 不匹配抛 TokenClaimsError"""
        bad_token = jose_jwt.encode(
            {
                "sub": "1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "jti": "fake",
                "iss": "evil-issuer",
                "aud": settings.JWT_AUDIENCE,
            },
            settings.EFFECTIVE_JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(TokenClaimsError) as exc_info:
            decode_token(bad_token)
        assert exc_info.value.code == "token_claims_invalid"

    def test_wrong_audience_raises_claims_error(self):
        """TC-JWT-11: aud 不匹配抛 TokenClaimsError"""
        bad_token = jose_jwt.encode(
            {
                "sub": "1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "jti": "fake",
                "iss": settings.JWT_ISSUER,
                "aud": "evil-audience",
            },
            settings.EFFECTIVE_JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(TokenClaimsError):
            decode_token(bad_token)

    def test_malformed_token_raises_malformed(self):
        """TC-JWT-12: 格式错误 (非 JWT) 抛 TokenMalformedError"""
        with pytest.raises(TokenMalformedError):
            decode_token("not-a-jwt-token")
        with pytest.raises(TokenMalformedError):
            decode_token("a.b")  # 段数不够
        with pytest.raises(TokenMalformedError):
            decode_token("a.b.c.d")  # 段数过多

    def test_empty_token_raises_malformed(self):
        """TC-JWT-13: 空字符串抛 TokenMalformedError"""
        with pytest.raises(TokenMalformedError):
            decode_token("")
        with pytest.raises(TokenMalformedError):
            decode_token(None)  # type: ignore[arg-type]

    def test_missing_claim_raises_missing_claim(self):
        """TC-JWT-14: 缺少 jti 抛 TokenMissingClaimError"""
        # 手工签一个不带 jti 的 token, 模拟外部服务签发但没注 jti 的情况
        bad_token = jose_jwt.encode(
            {
                "sub": "1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                # 没 jti
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
            },
            settings.EFFECTIVE_JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(TokenMissingClaimError) as exc_info:
            decode_token(bad_token)
        assert exc_info.value.code == "token_missing_claim"
        assert "jti" in str(exc_info.value)

    def test_decode_token_safe_returns_none_on_error(self):
        """TC-JWT-15: decode_token_safe 失败返回 None (兼容旧 API)"""
        assert decode_token_safe("bad-token") is None
        assert decode_token_safe("") is None
        # 有效 token 仍正常返回
        token = create_access_token({"sub": "1"})
        assert decode_token_safe(token) is not None


# ============== iss/aud 校验开关 ==============

class TestDecodeTokenOptions:
    """TC-JWT-16~17: decode_token 可选参数"""

    def test_verify_iss_aud_disabled(self):
        """TC-JWT-16: verify_iss_aud=False 时跳过 iss/aud 校验"""
        # 异构服务签发的 token, 可能是别的 iss/aud
        other_token = jose_jwt.encode(
            {
                "sub": "1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "jti": "x",
                "iss": "other-service",
                "aud": "other-aud",
            },
            settings.EFFECTIVE_JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        # 默认开启 iss/aud 校验 → 报错
        with pytest.raises(TokenClaimsError):
            decode_token(other_token)
        # 关闭后 → 正常
        payload = decode_token(other_token, verify_iss_aud=False)
        assert payload["iss"] == "other-service"
        assert payload["aud"] == "other-aud"


# ============== AuthService.issue_token 集成 ==============

class TestAuthServiceIssueToken:
    """TC-JWT-18~19: AuthService.issue_token 集成"""

    @staticmethod
    def _make_user_like(id: int, username: str, role: str = "admin", is_active: bool = True):
        """造一个鸭子类型的 User 对象, 避免触发完整 SQLAlchemy 映射

        AuthService.issue_token 只读 user.id / user.username / user.role,
        不触碰 DB, 所以 SimpleNamespace 即可.
        """
        from types import SimpleNamespace
        return SimpleNamespace(id=id, username=username, role=role, is_active=is_active)

    def test_issue_token_has_standardized_payload(self):
        """TC-JWT-18: issue_token 签发的 token 包含所有标准声明"""
        from app.auth.service.auth_service import AuthService

        user = self._make_user_like(7, "alice", "admin")
        token = AuthService.issue_token(user)  # type: ignore[arg-type]
        payload = decode_token(token)
        # 标准声明
        assert payload["sub"] == "7"
        assert payload["iss"] == settings.JWT_ISSUER
        assert payload["aud"] == settings.JWT_AUDIENCE
        assert "iat" in payload and "exp" in payload and "jti" in payload
        # 业务字段
        assert payload["role"] == "admin"
        assert payload["username"] == "alice"
        # 不应有冗余的 `id` 字段 (Phase-A 修复)
        assert "id" not in payload

    def test_issue_token_sub_is_str(self):
        """TC-JWT-19: sub 始终是字符串"""
        from app.auth.service.auth_service import AuthService

        user = self._make_user_like(999, "bob", "annotator")
        token = AuthService.issue_token(user)  # type: ignore[arg-type]
        payload = decode_token(token)
        assert payload["sub"] == "999"
        assert isinstance(payload["sub"], str)


# ============== 时钟漂移容忍 ==============

class TestLeeway:
    """TC-JWT-20~21: leeway 时钟漂移容忍"""

    def test_default_leeway_60s(self):
        """TC-JWT-20: 默认 leeway 60s, 允许 exp 已过但未超 60s 的 token"""
        # 手工造一个 iat 70s 前, exp 10s 前 的 token (默认 leeway=60s, 应仍可解码)
        now = int(time.time())
        token = jose_jwt.encode(
            {
                "sub": "1",
                "iat": now - 70,
                "exp": now - 10,  # 10s 前过期
                "jti": "x",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
            },
            settings.EFFECTIVE_JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        # 10s 前的过期, 在 60s leeway 内 → OK
        payload = decode_token(token)
        assert payload["sub"] == "1"

    def test_expired_beyond_leeway_raises(self):
        """TC-JWT-21: 过期超过 leeway 仍报错"""
        now = int(time.time())
        token = jose_jwt.encode(
            {
                "sub": "1",
                "iat": now - 200,
                "exp": now - 200,  # 200s 前过期
                "jti": "x",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
            },
            settings.EFFECTIVE_JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(TokenExpiredError):
            decode_token(token)
