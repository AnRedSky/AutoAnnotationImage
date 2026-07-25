"""
Auth API: Register / Login / Me / Logout (app/auth/api/)
=======================================================

**v3.0.0 审查修复**:
- 注册端点: 默认仅 admin 可创建账号; 首个 admin 走 bootstrap
- 业务逻辑全部委托 AuthService, 避免 controller 层重复实现
- 密码强度: 由 Pydantic schema 强制 (min 8 chars)
- 重复用户名: 409 Conflict (而非 400)

**v3.0.0 Stage 2.5 迁移**: 从 app/api/auth.py 迁入 auth 应用
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.middleware.http.auth import (
    get_current_user,
    require_admin,
)
from app.database import get_db
from app.schemas.auth import (
    RegisterRequest,
    TokenResponse,
    UserOut,
    ChangePasswordRequest,
)
# v3.0.0 审查修复: 业务委托 AuthService
from app.auth.service.auth_service import AuthService

router = APIRouter()


@router.post("/register", response_model=TokenResponse)
async def register(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),  # 修复: 仅 admin 可注册 (无 auth → 401, 非 admin → 403)
):
    """用户注册 (v3.0.0 修复: 仅管理员可创建账号, 防止任意提权)

    - 无 Authorization 头 → 401 (get_current_user 抛)
    - 已登录但非 admin → 403 (require_admin 抛)
    - 角色由后端按业务规则分配, 前端无法自选
    - 业务实现委托 AuthService.register (含密码长度/角色白名单/409 冲突)
    """
    # 修复 1: 业务全部下沉到 AuthService
    user = await AuthService.register(
        db,
        username=req.username,
        password=req.password,
        email=req.email,
        # admin 创建账号时允许指定角色, 普通用户注册入口已禁用
        role=req.role or "annotator",
    )

    token = AuthService.issue_token(user)
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
    """用户登录 (OAuth2 表单) — 委托 AuthService.login"""
    # 委托 AuthService 统一处理 (含 is_active 校验)
    result = await AuthService.login(db, form.username, form.password)
    return TokenResponse(
        access_token=result["access_token"],
        token_type=result["token_type"],
        user_id=result["user"]["id"],
    )


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改当前用户密码 (v3.0.0 新增, 修复: 之前 AuthService 有此方法但 API 缺失)"""
    await AuthService.change_password(
        db, current_user, body.old_password, body.new_password,
    )
    return {"success": True, "detail": "密码已更新"}
