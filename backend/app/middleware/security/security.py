"""
Security: Password Hashing & JWT (Middleware Layer)
=================================================

v3.0.0 迁移: 从 app.core.security 迁入 app.middleware.security.security
v3.0.0 审查修复 Phase-A: JWT 标准化 + 细分异常 + TokenPayload 类型化
v3.0.0 审查修复 Phase-B: Token 吊销 (jti + Redis 黑名单) 集成到 decode_token

**标准化 claims** (RFC 7519 + OWASP JWT Cheat Sheet):
- `sub`  : 用户 ID (字符串, 符合 JWT 规范)
- `iat`  : 签发时间 (Unix 秒)
- `exp`  : 过期时间 (Unix 秒)
- `jti`  : Token 唯一 ID (UUID4, 用于吊销/防重放)
- `iss`  : 签发方 (与 settings.JWT_ISSUER 匹配)
- `aud`  : 受众 (与 settings.JWT_AUDIENCE 匹配)
- `role` : 用户角色 (业务字段, 单独存放)

**细分异常** (避免所有 JWT 错误都被吞成 None):
- TokenExpiredError          → 401 + 提示刷新/重新登录
- TokenSignatureError        → 401 + 提示签名不匹配
- JWTClaimsError             → 401 + 提示 iss/aud 校验失败
- TokenMalformedError        → 401 + 提示格式错误
- TokenMissingClaimError     → 401 + 提示必填声明缺失
- TokenRevokedError          → 401 + 提示已被吊销 (Phase-B)
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from jose import jwt, JWTError
from jose.exceptions import (
    ExpiredSignatureError,
    JWTClaimsError,
)

from app.core.config import settings
from app.middleware.security import token_revocation

logger = logging.getLogger(__name__)


# ============== 密码哈希 (bcrypt) ==============

def hash_password(plain_password: str) -> str:
    """bcrypt 密码哈希"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


# 兼容旧名（部分测试 conftest 仍使用 get_password_hash）
get_password_hash = hash_password


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# ============== JWT 签发 (v3.0.0 标准化) ==============

# 必填声明白名单: 缺失任意一个视为非法 token
_REQUIRED_CLAIMS = ("sub", "exp", "iat", "jti", "iss", "aud")


def _utcnow_ts() -> int:
    """统一使用 timezone-aware UTC 时间戳 (秒)"""
    return int(datetime.now(tz=timezone.utc).timestamp())


def create_access_token(
    data: dict,
    expires_minutes: Optional[int] = None,
    *,
    jti: Optional[str] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    """生成标准化 JWT Token

    Args:
        data: 业务数据, 必填 `sub` 字段 (用户 ID, str/int)
        expires_minutes: 过期分钟数, 默认 settings.ACCESS_TOKEN_EXPIRE_MINUTES
        jti: 可选指定 jti, 默认自动生成 (UUID4 hex)
        extra_claims: 额外业务 claims (如 `role`), 合并到 payload

    Returns:
        签名后的 JWT 字符串

    标准化注入 (即使调用方未传, 也会自动补齐):
        - iat: 当前 UTC 时间戳
        - exp: iat + expires_minutes
        - jti: 唯一 token ID
        - iss: settings.JWT_ISSUER
        - aud: settings.JWT_AUDIENCE

    Note:
        - 调用方传的 iat/exp/jti/iss/aud 会被覆盖, 避免传入伪造值
        - 业务字段 (如 `role`) 通过 extra_claims 传入, 不污染标准声明
    """
    to_encode: dict[str, Any] = dict(data or {})

    now_ts = _utcnow_ts()
    minutes = expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    expire_ts = now_ts + int(minutes * 60)

    # sub 必须为字符串 (RFC 7519 §4.1.2)
    if "sub" in to_encode and not isinstance(to_encode["sub"], str):
        to_encode["sub"] = str(to_encode["sub"])

    # 标准声明 (强制覆盖, 防止业务侧传入伪造值)
    to_encode["iat"] = now_ts
    to_encode["exp"] = expire_ts
    to_encode["jti"] = jti or secrets.token_hex(16)
    to_encode["iss"] = settings.JWT_ISSUER
    to_encode["aud"] = settings.JWT_AUDIENCE

    # 额外业务 claims
    if extra_claims:
        for k, v in extra_claims.items():
            if k in _REQUIRED_CLAIMS:
                logger.warning(
                    "create_access_token: 跳过保留声明 %r, 改用 extra_claims 不安全",
                    k,
                )
                continue
            to_encode[k] = v

    return jwt.encode(
        to_encode,
        settings.EFFECTIVE_JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


# ============== JWT 解码 (v3.0.0 细分异常) ==============

# 自定义异常, 供上层 (auth middleware) 按类型返回 401/403
class TokenValidationError(Exception):
    """Token 验证失败基类"""
    code: str = "token_invalid"
    http_status: int = 401

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        if code:
            self.code = code


class TokenExpiredError(TokenValidationError):
    """Token 已过期 (exp < now)"""
    code = "token_expired"
    http_status = 401


class TokenSignatureError(TokenValidationError):
    """Token 签名不匹配 (secret/wrong alg)"""
    code = "token_signature_invalid"
    http_status = 401


class TokenClaimsError(TokenValidationError):
    """iss / aud / sub 等 claims 校验失败"""
    code = "token_claims_invalid"
    http_status = 401


class TokenMalformedError(TokenValidationError):
    """Token 格式非法 (非 base64 / 段数错误)"""
    code = "token_malformed"
    http_status = 401


class TokenMissingClaimError(TokenValidationError):
    """必填声明缺失"""
    code = "token_missing_claim"
    http_status = 401


class TokenRevokedError(TokenValidationError):
    """Token 已被吊销 (jti 在黑名单, 或用户级吊销时间戳 > iat)

    Phase-B 新增: 改密 / 登出 / 封号 场景触发
    """
    code = "token_revoked"
    http_status = 401


def decode_token(
    token: str,
    *,
    verify_iss_aud: bool = True,
    leeway: Optional[int] = None,
    check_revocation: bool = True,
) -> dict:
    """解码并校验 JWT Token

    Args:
        token: 待校验的 JWT 字符串
        verify_iss_aud: 是否校验 iss/aud 声明, 默认 True
        leeway: 时钟漂移容忍 (秒), 默认 settings.JWT_LEEWAY_SECONDS
        check_revocation: 是否检查吊销 (Phase-B), 默认 True

    Returns:
        解码后的 payload dict (含所有 claims)

    Raises:
        TokenExpiredError: token 已过期
        TokenSignatureError: 签名校验失败
        TokenClaimsError: iss/aud 校验失败
        TokenMalformedError: 格式非法
        TokenMissingClaimError: 必填声明缺失
        TokenRevokedError: token 已被吊销 (Phase-B)
        TokenValidationError: 其它 JWT 错误 (基类)

    设计:
        - 不返回 None, 一律抛异常, 让调用方按 http_status 决定响应码
        - 保留对 jose 原生异常的兼容: ExpiredSignatureError/JWTClaimsError
        - 对 jose 抛出的通用 JWTError, 进一步按 message 区分
          (避免误把签名错误当成过期/claims 错误)
        - 吊销检查: 优先检查 jti 黑名单, 再检查用户级吊销时间戳
    """
    if not token or not isinstance(token, str):
        raise TokenMalformedError("Token is empty or not a string")

    options = {
        "verify_signature": True,
        "verify_exp": True,
        "verify_iat": True,
        "verify_nbf": False,  # 未启用 nbf 声明
        "verify_aud": verify_iss_aud,
        "verify_iss": verify_iss_aud,
    }
    decode_kwargs: dict[str, Any] = {
        "algorithms": [settings.JWT_ALGORITHM],
        "options": options,
    }
    if verify_iss_aud:
        decode_kwargs["audience"] = settings.JWT_AUDIENCE
        decode_kwargs["issuer"] = settings.JWT_ISSUER
    actual_leeway = int(leeway if leeway is not None else settings.JWT_LEEWAY_SECONDS)
    if actual_leeway > 0:
        decode_kwargs["options"]["leeway"] = actual_leeway

    try:
        payload = jwt.decode(token, settings.EFFECTIVE_JWT_SECRET, **decode_kwargs)
    except ExpiredSignatureError as e:
        logger.info("decode_token: token expired (%s)", e)
        raise TokenExpiredError("Token has expired") from e
    except JWTClaimsError as e:
        # iss / aud 不匹配
        logger.info("decode_token: claims error (%s)", e)
        raise TokenClaimsError(f"Token claims invalid: {e}") from e
    except JWTError as e:
        # 包含: InvalidSignatureError / DecodeError / 其他
        msg = str(e).lower()
        if "signature" in msg or "verification" in msg:
            logger.warning("decode_token: signature error (%s)", e)
            raise TokenSignatureError("Token signature is invalid") from e
        if "decode" in msg or "format" in msg or "segment" in msg:
            logger.warning("decode_token: malformed (%s)", e)
            raise TokenMalformedError(f"Token is malformed: {e}") from e
        logger.warning("decode_token: generic JWTError (%s)", e)
        raise TokenValidationError(f"Token validation failed: {e}") from e
    except ValueError as e:
        # jose 在某些坏 token 上抛 ValueError (如 base64 解码失败)
        logger.warning("decode_token: value error (%s)", e)
        raise TokenMalformedError(f"Token decode failed: {e}") from e

    # 必填声明兜底校验 (jose 已 verify_iat/verify_exp, 这里再 check jti/aud/iss 完整性)
    missing = [c for c in _REQUIRED_CLAIMS if c not in payload]
    if missing:
        logger.warning("decode_token: missing required claims: %s", missing)
        raise TokenMissingClaimError(
            f"Token missing required claims: {','.join(missing)}"
        )

    # Phase-B: 吊销检查 (jti + 用户级)
    if check_revocation:
        jti = payload.get("jti")
        sub = payload.get("sub")
        iat = payload.get("iat")
        user_id_int: Optional[int] = None
        try:
            if sub is not None:
                user_id_int = int(sub)
        except (TypeError, ValueError):
            user_id_int = None
        if token_revocation.check_revoked(
            jti=jti, user_id=user_id_int, iat=iat,
        ):
            logger.warning(
                "decode_token: token revoked (jti=%s, user_id=%s)",
                (jti[:8] if jti else None), user_id_int,
            )
            raise TokenRevokedError("Token has been revoked")

    return payload


# ============== 便捷别名 (保留旧 API 兼容) ==============
# 注意: 旧 API 返回 None 表示失败, 新 API 抛异常. 保留旧名给只关心布尔结果的调用方
def decode_token_safe(token: str) -> Optional[dict]:
    """兼容旧 API: 解码失败返回 None 而不是抛异常

    新代码请直接用 decode_token + 捕获 TokenValidationError 子类
    """
    try:
        return decode_token(token)
    except TokenValidationError as e:
        logger.debug("decode_token_safe: %s (%s)", e.code, e)
        return None


__all__ = [
    # 密码
    "hash_password",
    "get_password_hash",
    "verify_password",
    # JWT 签发
    "create_access_token",
    # JWT 解码
    "decode_token",
    "decode_token_safe",
    # 异常
    "TokenValidationError",
    "TokenExpiredError",
    "TokenSignatureError",
    "TokenClaimsError",
    "TokenMalformedError",
    "TokenMissingClaimError",
    "TokenRevokedError",
]
