"""
Auth API: Register / Login / Me / Logout (app/auth/api/)
=======================================================

**v3.0.0 Stage 2.5 迁移**: 从 app/api/auth.py 迁入 auth 应用
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.schemas.auth import RegisterRequest, TokenResponse, UserOut

router = APIRouter()


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """用户注册"""
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        email=req.email,
        role=req.role or "annotator",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return TokenResponse(access_token=token, token_type="bearer", user_id=user.id)


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return UserOut(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """登出接口 (前端清除 Token 即可)"""
    return {"detail": "已退出登录", "user_id": current_user.id}


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """用户登录 (OAuth2 表单)"""
    result = await db.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User disabled")

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return TokenResponse(access_token=token, token_type="bearer", user_id=user.id)
