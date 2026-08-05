"""User Management API (app/admin/api/) — Admin only

**v3.0.0 Stage 2.5 迁移**: 从 app/api/user.py 迁入 admin 应用
**v3.0.0 Phase D 修复**: 业务全部下沉到 UserService, API 只做参数解析和 HTTP 适配
**v3.3.0 完善**: 新增创建用户 / 删除用户 + 个人中心 (改昵称/邮箱/密码)
**v3.3.1 L2**: 新增 GET /search (供团队邀请下拉远程搜索, 任何已登录用户可用)
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.middleware.http.auth import require_admin, get_current_user
from app.database import get_db
from app.admin.service.user_service import UserService

router = APIRouter()


def _iso_utc(dt):
    """序列化 datetime 为带 Z 后缀的 UTC ISO 字符串 (前端 JS 可正确解析).

    MySQL DATETIME 列不带时区 — ORM 读出是 naive datetime, 但实际存的就是
    ``datetime.utcnow()`` 写入的 UTC 值. 补 'Z' 让前端 ``new Date(s)`` 当作 UTC 解析,
    再 ``.toLocaleString('zh-CN')`` 转北京时间显示.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.isoformat()
    return dt.isoformat() + "Z"


# ============== Schemas ==============

class ChangeRoleRequest(BaseModel):
    new_role: str = Field(..., description="新角色: super_admin/admin/annotator/viewer")


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    email: str | None = Field(None, max_length=100)
    role: str = Field("annotator", description="角色: super_admin/admin/annotator/viewer")


class UpdateProfileRequest(BaseModel):
    email: str | None = Field(None, max_length=100)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=128)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=128)


# ============== 列表 ==============

@router.get("/")
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """用户列表

    v3.3.0 P0 修复: 严格按角色过滤敏感信息
    - admin: 看完整信息 (含 email, role, is_active 等)
    - 普通用户: 仅看 id + username (供团队邀请成员时下拉选择用户名)
      不暴露 email / role / is_active, 避免隐私泄露
    """
    users = await UserService.list_active(db, skip=0, limit=1000)
    is_admin = current_user.is_admin()
    return {
        "items": [
            {
                "id": u.id,
                "username": u.username,
                # 敏感字段: 仅 admin 可见
                "email": u.email if is_admin else None,
                "role": u.role if is_admin else None,
                "is_active": u.is_active if is_admin else None,
                "created_at": _iso_utc(u.created_at) if is_admin else None,
                "last_login_at": _iso_utc(u.last_login_at) if is_admin else None,
            }
            for u in users
        ],
        "total": len(users),
    }


# ============== 用户搜索 (v3.3.1 L2) ==============
# 必须定义在 GET /{user_id} 之前, 避免被路由参数吞掉
@router.get("/search")
async def search_users(
    q: str = Query(..., min_length=1, max_length=50, description="搜索关键词 (按 username 模糊匹配)"),
    limit: int = Query(20, ge=1, le=50, description="最大返回数量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """v3.3.1 L2: 用户搜索 (供团队邀请下拉, 任何已登录用户可用).

    业务规则:
    - 任何已登录用户可调用 (不再限制 admin)
    - 排除当前用户自己
    - 仅返回 id + username (不暴露 email/role/is_active 等敏感信息)
    - 模糊匹配 username (大小写不敏感)
    - 仅返回 is_active=True 的用户
    - 按 username 升序, 限制最大 limit 条
    """
    keyword = q.strip()
    if not keyword:
        return {"items": [], "total": 0}

    # SQLAlchemy 模糊匹配: ilike (PostgreSQL 大小写不敏感)
    # MySQL 默认 utf8mb4_general_ci 也大小写不敏感
    # SQLite: LIKE 默认大小写不敏感 (仅 ASCII 字符)
    # 兼容处理: 用 LIKE + 两侧都加 % 让任意子串匹配
    pattern = f"%{keyword}%"
    result = await db.execute(
        select(User)
        .where(
            User.is_active == True,  # noqa: E712
            User.id != current_user.id,
            User.username.like(pattern),
        )
        .order_by(User.username.asc())
        .limit(limit)
    )
    users = result.scalars().all()
    return {
        "items": [
            {"id": u.id, "username": u.username}
            for u in users
        ],
        "total": len(users),
    }


# ============== 个人中心 (本人操作, 不需要 admin, 必须在 /{user_id} 之前) ==============

@router.get("/me/profile")
async def get_my_profile(
    current_user: User = Depends(get_current_user),
):
    """获取当前用户个人信息"""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "created_at": _iso_utc(current_user.created_at),
        "last_login_at": _iso_utc(current_user.last_login_at),
    }


@router.put("/me/profile")
async def update_my_profile(
    body: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改个人信息 (邮箱)"""
    current_user.email = body.email
    await db.commit()
    await db.refresh(current_user)
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
    }


@router.post("/me/change-password")
async def change_my_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改密码 (需要旧密码验证)"""
    from app.middleware.security.security import verify_password, hash_password

    if not verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(400, "旧密码不正确")

    if len(body.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")

    current_user.password_hash = hash_password(body.new_password)
    await db.commit()
    return {"success": True, "detail": "密码修改成功"}


# ============== 创建用户 ==============

@router.post("/")
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建用户 (仅管理员)"""
    from app.middleware.security.security import hash_password

    # 角色校验
    valid_roles = ("super_admin", "admin", "annotator", "viewer")
    if body.role not in valid_roles:
        raise HTTPException(400, f"Invalid role: {body.role}")

    # 用户名唯一检查
    existing = await UserService.get_by_username(db, body.username)
    if existing:
        raise HTTPException(409, f"Username '{body.username}' already exists")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        email=body.email,
        role=body.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
    }


# ============== 删除用户 ==============

@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除用户 (仅管理员, 不能删自己)"""
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")

    user = await UserService.get(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    # super_admin 不能被非 super_admin 删除
    if user.role == "super_admin" and not admin.is_super_admin():
        raise HTTPException(403, "Cannot delete super_admin")

    await db.delete(user)
    await db.commit()
    return {"success": True, "detail": f"User {user_id} deleted"}


# ============== 详情 ==============

@router.get("/{user_id}")
async def get_user_detail(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用户详情 (管理员)"""
    user = await UserService.get(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ============== 启停 ==============

@router.post("/{user_id}/activate")
async def activate_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """激活用户"""
    user = await UserService.get(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    await UserService.activate(db, user)
    return {"id": user.id, "is_active": user.is_active}


@router.post("/{user_id}/deactivate")
async def deactivate_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """停用用户 (不能停用自己)"""
    user = await UserService.get(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot deactivate yourself")
    await UserService.deactivate(db, user)
    return {"id": user.id, "is_active": user.is_active}


# ============== 角色变更 ==============

@router.post("/{user_id}/role")
async def change_user_role(
    user_id: int,
    body: ChangeRoleRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """修改用户角色

    v3.3.4 (P0-3 安全加固): 角色变更后立即吊销该用户的所有 token
      - 防止权限收敛延迟: 旧 token 在角色变更后到下次刷新前仍可使用,
        可能造成「已降级但仍能短暂访问管理接口」的窗口期
      - 强制用户重新登录, 获得符合新角色的权限
      - super_admin 不能被非 super_admin 降级, 业务保护已就位
    """
    user = await UserService.get(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id and body.new_role not in ("admin", "super_admin"):
        raise HTTPException(400, "Cannot demote yourself from admin")
    old_role = user.role
    await UserService.change_role(db, user, body.new_role)

    # v3.3.4 (P0-3): 角色变更后吊销该用户所有 token
    # - 用户级吊销通过 jwt:revoked:user:{id} 时间戳实现
    # - 后续签发的 token 必须 iat > 时间戳, 否则视为已吊销
    # - 旧 token (iat 早于吊销时间戳) 在 decode_token 时被拒绝
    from app.middleware.security.token_revocation import revoke_user
    revoke_user(user.id)

    # v3.3.4: 审计 (角色变更属高敏感操作)
    from app.tasks.service.audit_service import log_audit
    try:
        await log_audit(
            db,
            user_id=admin.id,
            event_type="user_role_changed",
            resource_type="user",
            resource_id=user.id,
            detail={"old_role": old_role, "new_role": body.new_role, "tokens_revoked": True},
        )
    except Exception:
        # 审计失败不影响主流程
        pass

    return {"id": user.id, "role": user.role}


@router.post("/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """重置用户密码 (仅管理员, 不需要旧密码)"""
    from app.middleware.security.security import hash_password

    user = await UserService.get(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.role == "super_admin" and not admin.is_super_admin():
        raise HTTPException(403, "Cannot reset super_admin password")

    user.password_hash = hash_password(body.new_password)
    await db.commit()
    return {"success": True, "detail": f"User {user_id} password reset"}
