"""
FastAPI Auth Dependencies (Middleware Layer)
============================================

v3.0.0 迁移: 从 app.middleware.http.auth 迁入 app.middleware.http.auth
v3.0.0 审查修复 Phase-A: 按 TokenValidationError 子类型区分 HTTP 状态码
- TokenExpiredError / Signature / Claims / Malformed / Missing  → 401
- 其它业务异常 (user.is_active=False 等) → 401
- 角色不足 → 403
v3.0.0 审查修复 Phase-B: 新增 get_current_user_with_payload (供 logout 拿 jti/exp 吊销)
"""
from typing import NamedTuple, Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.middleware.security.security import (
    decode_token,
    TokenValidationError,
    TokenExpiredError,
    TokenSignatureError,
    TokenClaimsError,
    TokenMalformedError,
    TokenMissingClaimError,
    TokenRevokedError,
)
from app.admin.model.user import User


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def _http401_unauthorized(detail: str, code: str) -> HTTPException:
    """统一 401 响应, 携带 WWW-Authenticate 头 + 错误码"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


# ============== Phase-B: 返回 User + Token payload 上下文 ==============

class CurrentUserContext(NamedTuple):
    """当前用户 + Token 原始 payload 上下文

    用于 logout / change-password 等需要 jti/exp 的场景.
    NamedTuple 兼容 Pydantic 序列化, 也可直接解构:
        ctx = await get_current_user_with_payload(...)
        user, payload, raw_token = ctx
    """
    user: User
    payload: dict
    raw_token: str


async def _decode_token_to_user_payload(
    token: str, db: AsyncSession,
) -> tuple[dict, User]:
    """单点解码: token → payload + DB User, 异常统一抛 401.

    内部辅助方法, 供 get_current_user / get_current_user_with_payload 共用,
    避免重复实现异常映射和 DB 查询逻辑.
    """
    try:
        payload = decode_token(token)
    except TokenExpiredError as e:
        raise _http401_unauthorized("Token has expired, please log in again", e.code)
    except TokenSignatureError as e:
        raise _http401_unauthorized("Token signature is invalid", e.code)
    except TokenClaimsError as e:
        raise _http401_unauthorized(f"Token claims invalid: {e}", e.code)
    except TokenMalformedError as e:
        raise _http401_unauthorized("Token is malformed", e.code)
    except TokenMissingClaimError as e:
        raise _http401_unauthorized(f"Token missing required claims: {e}", e.code)
    except TokenRevokedError as e:
        raise _http401_unauthorized("Token has been revoked (password changed or logged out)", e.code)
    except TokenValidationError as e:
        raise _http401_unauthorized("Could not validate credentials", e.code)

    user_id = payload.get("sub")
    if not user_id:
        raise _http401_unauthorized("Token missing subject", "token_missing_claim")
    try:
        uid_int = int(user_id)
    except (TypeError, ValueError):
        raise _http401_unauthorized("Token subject is not a valid user id", "token_invalid_subject")

    result = await db.execute(select(User).where(User.id == uid_int))
    user = result.scalar_one_or_none()
    if user is None:
        raise _http401_unauthorized("User no longer exists", "user_not_found")
    if not user.is_active:
        raise _http401_unauthorized("User is inactive", "user_inactive")
    return payload, user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """通过 JWT Token 获取当前登录用户

    v3.0.0 审查修复 Phase-A: 按异常类型返回不同 detail, 便于前端区分
    v3.0.0 审查修复 Phase-B: 新增 token_revoked 处理 (改密/登出后旧 token 失效)
    - token_expired            → 提示用户重新登录/刷新
    - token_signature_invalid  → 提示 token 被篡改, 强制重新登录
    - token_claims_invalid     → 提示 iss/aud 不匹配, 多服务共用时排查
    - token_malformed          → 提示 token 格式错误, 一般是前端拼接问题
    - token_missing_claim      → 提示 token 字段缺失, 升级后旧 token 失效
    - token_revoked            → 提示 token 已被吊销, 改密/登出后失效

    注: 仅需 user 的场景, 直接 Depends(get_current_user).
       需同时拿 jti/exp 的场景 (logout), 用 get_current_user_with_payload.
    """
    _payload, user = await _decode_token_to_user_payload(token, db)
    return user


async def get_current_user_with_payload(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUserContext:
    """获取当前登录用户 + token 原始 payload (Phase-B 新增)

    适用场景: logout 需要 jti+exp 写入吊销黑名单.
    - 同一 token 只 decode 一次, 避免重复验签 / 重复吊销检查
    - 返回 NamedTuple, 可解构: user, payload, raw_token = ctx
    """
    payload, user = await _decode_token_to_user_payload(token, db)
    return CurrentUserContext(user=user, payload=payload, raw_token=token)


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求管理员权限"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return current_user


# ============== v2.5.35 新增: SSE 用可选鉴权 ==============
# 复用 SSE 端点 (EventSource 无法设 Authorization header, 走 query ?token=xxx 兜底).
# 之前在 training.py 内 inline 实现, 现在提升到 core/deps 供 detection/segmentation 共用.

async def get_user_optional_for_query(
    request: Request,
    token: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """可选鉴权 (支持 query token) — 与原 training.py 实现完全等价.

    - Authorization header 优先
    - 其次 query ?token=xxx
    - 都没有 → None (允许匿名访问, 内部场景)
    - 有 token 但无效 → 401 (按异常类型细化 detail)
    """
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None

    try:
        payload = decode_token(token)
    except TokenExpiredError as e:
        raise _http401_unauthorized("Token has expired", e.code)
    except TokenSignatureError as e:
        raise _http401_unauthorized("Token signature is invalid", e.code)
    except TokenClaimsError as e:
        raise _http401_unauthorized(f"Token claims invalid: {e}", e.code)
    except TokenMalformedError as e:
        raise _http401_unauthorized("Token is malformed", e.code)
    except TokenMissingClaimError as e:
        raise _http401_unauthorized(f"Token missing required claims: {e}", e.code)
    except TokenRevokedError as e:
        raise _http401_unauthorized("Token has been revoked", e.code)
    except TokenValidationError as e:
        raise _http401_unauthorized("Could not validate credentials", e.code)

    user_id = payload.get("sub")
    if not user_id:
        raise _http401_unauthorized("Token missing subject", "token_missing_claim")
    try:
        uid_int = int(user_id)
    except (TypeError, ValueError):
        raise _http401_unauthorized("Token subject is not a valid user id", "token_invalid_subject")

    try:
        result = await db.execute(select(User).where(User.id == uid_int))
        user = result.scalar_one_or_none()
    except Exception:
        user = None
    if not user:
        raise _http401_unauthorized("User no longer exists", "user_not_found")
    if not user.is_active:
        raise _http401_unauthorized("User is inactive", "user_inactive")
    return user
