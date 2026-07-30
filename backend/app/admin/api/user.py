"""User Management API (app/admin/api/) — Admin only

**v3.0.0 Stage 2.5 迁移**: 从 app/api/user.py 迁入 admin 应用
**v3.0.0 Phase D 修复**: 业务全部下沉到 UserService, API 只做参数解析和 HTTP 适配
**v3.3.0 完善**: 新增创建用户 / 删除用户 + 个人中心 (改昵称/邮箱/密码)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.middleware.http.auth import require_admin, get_current_user
from app.database import get_db
from app.admin.service.user_service import UserService

router = APIRouter()


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
    admin: User = Depends(require_admin),
):
    """用户列表 (管理员视角)"""
    users = await UserService.list_active(db, skip=0, limit=1000)
    return {
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
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
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
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
    """修改用户角色"""
    user = await UserService.get(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id and body.new_role not in ("admin", "super_admin"):
        raise HTTPException(400, "Cannot demote yourself from admin")
    await UserService.change_role(db, user, body.new_role)
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
