"""User Management API (app/admin/api/) — Admin only

**v3.0.0 Stage 2.5 迁移**: 从 app/api/user.py 迁入 admin 应用
**v3.0.0 Phase D 修复**: 业务全部下沉到 UserService, API 只做参数解析和 HTTP 适配
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.middleware.http.auth import require_admin
from app.database import get_db
from app.admin.service.user_service import UserService

router = APIRouter()


# ============== Schemas ==============

class ChangeRoleRequest(BaseModel):
    new_role: str = Field(..., description="新角色: admin/annotator/viewer")


# ============== 列表 ==============

@router.get("/")
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用户列表 (管理员视角)

    v3.0.0 Phase D: 委托 UserService.list_active
    """
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


# ============== 详情 ==============

@router.get("/{user_id}")
async def get_user_detail(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用户详情 (Phase D 修复: 委托 UserService.get)"""
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
    """激活用户 (Phase D 新增: 委托 UserService.activate)"""
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
    """停用用户 (Phase D 新增: 业务规则由 Service 校验)

    业务规则: 管理员不可停用自己 (防止最后一个 admin 误停导致无法登录)
    """
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
    """修改用户角色 (Phase D 新增: 委托 UserService.change_role)"""
    user = await UserService.get(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    # 业务规则: 不能撤销自己的 admin 权限
    if user.id == admin.id and body.new_role != "admin":
        raise HTTPException(400, "Cannot demote yourself from admin")
    await UserService.change_role(db, user, body.new_role)
    return {"id": user.id, "role": user.role}
