"""
Middleware Security Subpackage
==============================

**v3.0.0 Phase-A**: JWT 标准化 + 细分异常
**v3.0.0 Phase-B**: Token 吊销 (jti + Redis 黑名单)

依赖:
- app.core.config (settings)
- app.database.redis (redis_client, 用于 token 黑名单)
"""
from app.middleware.security.security import (  # noqa: F401
    # 密码
    hash_password,
    get_password_hash,
    verify_password,
    # JWT 签发 / 解码
    create_access_token,
    decode_token,
    decode_token_safe,
    # 异常
    TokenValidationError,
    TokenExpiredError,
    TokenSignatureError,
    TokenClaimsError,
    TokenMalformedError,
    TokenMissingClaimError,
    TokenRevokedError,
)
from app.middleware.security import token_revocation  # noqa: F401

__all__ = [
    # 密码
    "hash_password",
    "get_password_hash",
    "verify_password",
    # JWT
    "create_access_token",
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
    # 吊销
    "token_revocation",
]
