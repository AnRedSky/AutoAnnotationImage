"""
FastAPI Auth Dependencies (Middleware Layer)
============================================

v3.0.0 迁移: 从 app.core.deps 迁入 app.middleware.http.auth
"""
from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.middleware.security.security import decode_token
from app.model.user import User


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """通过 JWT Token 获取当前登录用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_token(token)
    if payload is None:
        raise credentials_exception
    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


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
    - 有 token 但无效 → 401
    """
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        raise HTTPException(401, "Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"})
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Could not validate credentials")
    try:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
    except Exception:
        user = None
    if not user or not user.is_active:
        raise HTTPException(401, "Could not validate credentials")
    return user
