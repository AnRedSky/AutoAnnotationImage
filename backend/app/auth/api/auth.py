"""
Auth API: Register / Login / Me / Logout / ChangePassword (app/auth/api/)
======================================================================

**v3.0.0 审查修复**:
- 注册端点: 公开接口, 任何人可自主注册 (角色固定 annotator, 防止任意提权)
- 业务逻辑全部委托 AuthService, 避免 controller 层重复实现
- 密码强度: 由 Pydantic schema 强制 (min 8 chars)
- 重复用户名: 409 Conflict (而非 400)

**v3.0.0 Stage 2.5 迁移**: 从 app/api/auth.py 迁入 auth 应用

**v3.0.0 审查修复 Phase-B**:
- logout: 使用 get_current_user_with_payload 拿到 jti/exp, 走 AuthService.logout 吊销
- change-password: 用户所有 token 已在 AuthService.change_password 内被吊销
- login/register: 审计事件已由 AuthService 内置 emit
"""
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.middleware.http.auth import (
    get_current_user,
    get_current_user_with_payload,
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
):
    """用户注册 (公开接口, 任何人可自主注册)

    - 无需认证, 任何人可直接注册
    - 角色固定为 annotator (后端强制, 防止任意提权)
    - 密码强度: Schema 层强制至少 8 位
    - 重复用户名: 409 Conflict
    - 业务实现委托 AuthService.register (含密码长度/角色白名单/409 冲突/审计事件)
    """
    user = await AuthService.register(
        db,
        username=req.username,
        password=req.password,
        email=req.email,
        role="annotator",  # 公开注册固定 annotator, 忽略请求中的 role 字段
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
async def logout(
    ctx = Depends(get_current_user_with_payload),
):
    """登出接口 (Phase-B: 主动吊销当前 token 的 jti)

    - 拿到 token 的 jti + exp, 写入 Redis 黑名单
    - 后续该 token 即便未过期, 也会被 decode_token 拒绝 (TokenRevokedError)
    - 审计: emit LOGOUT 事件, 记录 user_id / jti / IP
    - 前端无需做任何操作 (后端已主动吊销)

    Phase-B 安全增强: 即便前端不删 localStorage, 旧 token 也不能再被使用
    """
    jti = ctx.payload.get("jti")
    exp = ctx.payload.get("exp")
    return AuthService.logout(ctx.user, jti=jti, exp_ts=exp)


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """用户登录 (OAuth2 表单) — 委托 AuthService.login

    Phase-B: 登录成功/失败事件已由 AuthService 内置 emit
    """
    # 委托 AuthService 统一处理 (含 is_active 校验 + 审计)
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
    """修改当前用户密码 (v3.0.0 新增, 修复: 之前 AuthService 有此方法但 API 缺失)

    Phase-B 安全增强:
    - 改密成功后, AuthService 会调用 token_revocation.revoke_user(user.id)
    - 该用户所有未过期 token 立即失效, 需重新登录
    - 审计: emit CHANGE_PASSWORD 事件, 记录 user_id / IP
    """
    await AuthService.change_password(
        db, current_user, body.old_password, body.new_password,
    )
    return {"success": True, "detail": "密码已更新, 请重新登录"}
