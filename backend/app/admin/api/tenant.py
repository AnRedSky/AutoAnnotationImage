"""
Tenant Management API (v3.2.0 MT-7)
====================================

多租户管理端点:
  - GET    /api/tenants                  列出租户 (super_admin only)
  - POST   /api/tenants                  创建租户 (super_admin only)
  - GET    /api/tenants/{id}            租户详情
  - PATCH  /api/tenants/{id}/status     停用/激活租户
  - GET    /api/tenants/{id}/users      租户用户列表
  - POST   /api/tenants/{id}/users      分配用户到租户 (tenant_admin+)
  - DELETE /api/tenants/{id}/users/{uid} 移除用户 (tenant_admin+)
  - POST   /api/datasets/{id}/share     共享 dataset (owner/admin)
  - DELETE /api/datasets/{id}/share/{uid} 取消共享
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.admin.model.user import User
from app.admin.model.tenant import Tenant, TENANT_STATUS_ACTIVE, TENANT_STATUS_SUSPENDED
from app.admin.model.user_tenant_role import UserTenantRole, TENANT_ROLES
from app.tasks.model.dataset import Dataset
from app.tasks.model.dataset_membership import DatasetMembership, DATASET_SHARE_ROLES
from app.middleware.http.auth import get_current_user, require_admin

router = APIRouter()


# ============== Schemas ==============

class TenantCreate(BaseModel):
    name: str
    slug: str
    max_users: int = 50
    max_datasets: int = 100


class TenantStatusUpdate(BaseModel):
    status: str  # active | suspended


class UserRoleAssign(BaseModel):
    user_id: int
    role: str = "annotator"  # tenant_admin | annotator | viewer


class DatasetShare(BaseModel):
    user_id: int
    role: str = "viewer"  # annotator | viewer


# ============== Tenant CRUD (super_admin) ==============

@router.get("")
async def list_tenants(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出所有租户 (super_admin only)."""
    if not current_user.is_super_admin():
        raise HTTPException(403, "Super admin permission required")
    result = await db.execute(select(Tenant).order_by(Tenant.id))
    tenants = result.scalars().all()
    return {
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "status": t.status,
                "max_users": t.max_users,
                "max_datasets": t.max_datasets,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tenants
        ]
    }


@router.post("")
async def create_tenant(
    body: TenantCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建租户 (super_admin only)."""
    if not current_user.is_super_admin():
        raise HTTPException(403, "Super admin permission required")

    # slug 唯一检查
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Tenant slug '{body.slug}' already exists")

    tenant = Tenant(
        name=body.name,
        slug=body.slug,
        status=TENANT_STATUS_ACTIVE,
        max_users=body.max_users,
        max_datasets=body.max_datasets,
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "status": tenant.status,
    }


@router.get("/{tenant_id}")
async def get_tenant(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """租户详情."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    # 权限: super_admin 或同 tenant 的 user
    if not current_user.is_super_admin() and current_user.tenant_id != tenant_id:
        raise HTTPException(403, "无权限查看此租户")
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "status": tenant.status,
        "max_users": tenant.max_users,
        "max_datasets": tenant.max_datasets,
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
    }


@router.patch("/{tenant_id}/status")
async def update_tenant_status(
    tenant_id: int,
    body: TenantStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """停用/激活租户 (super_admin only)."""
    if not current_user.is_super_admin():
        raise HTTPException(403, "Super admin permission required")
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    if body.status not in (TENANT_STATUS_ACTIVE, TENANT_STATUS_SUSPENDED):
        raise HTTPException(400, f"Invalid status: {body.status}")
    tenant.status = body.status
    await db.commit()
    return {"id": tenant.id, "status": tenant.status}


# ============== 租户用户管理 ==============

@router.get("/{tenant_id}/users")
async def list_tenant_users(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """租户用户列表 (tenant_admin+)."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    # 权限: super_admin 或同 tenant 的 admin
    if not current_user.is_super_admin():
        if current_user.tenant_id != tenant_id:
            raise HTTPException(403, "无权限查看此租户用户")
        if not current_user.is_admin():
            raise HTTPException(403, "Tenant admin permission required")

    result = await db.execute(
        select(UserTenantRole, User)
        .join(User, UserTenantRole.user_id == User.id)
        .where(UserTenantRole.tenant_id == tenant_id)
        .order_by(User.id)
    )
    rows = result.all()
    return {
        "items": [
            {
                "user_id": r[1].id,
                "username": r[1].username,
                "email": r[1].email,
                "role": r[0].role,
                "assigned_at": r[0].assigned_at.isoformat() if r[0].assigned_at else None,
            }
            for r in rows
        ]
    }


@router.post("/{tenant_id}/users")
async def assign_user_to_tenant(
    tenant_id: int,
    body: UserRoleAssign,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分配用户到租户 (tenant_admin+)."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    if not current_user.is_super_admin():
        if current_user.tenant_id != tenant_id:
            raise HTTPException(403, "无权限管理此租户")
        if not current_user.is_admin():
            raise HTTPException(403, "Tenant admin permission required")

    if body.role not in TENANT_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}, must be {TENANT_ROLES}")

    # 用户是否存在
    user = await db.get(User, body.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    # 已存在检查 (幂等)
    existing = await db.execute(
        select(UserTenantRole).where(
            UserTenantRole.user_id == body.user_id,
            UserTenantRole.tenant_id == tenant_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "User already assigned to this tenant")

    utr = UserTenantRole(
        user_id=body.user_id,
        tenant_id=tenant_id,
        role=body.role,
        assigned_by=current_user.id,
    )
    db.add(utr)
    await db.commit()
    return {
        "user_id": utr.user_id,
        "tenant_id": utr.tenant_id,
        "role": utr.role,
    }


@router.delete("/{tenant_id}/users/{user_id}")
async def remove_user_from_tenant(
    tenant_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从租户移除用户 (tenant_admin+)."""
    if not current_user.is_super_admin():
        if current_user.tenant_id != tenant_id:
            raise HTTPException(403, "无权限管理此租户")
        if not current_user.is_admin():
            raise HTTPException(403, "Tenant admin permission required")

    result = await db.execute(
        select(UserTenantRole).where(
            UserTenantRole.user_id == user_id,
            UserTenantRole.tenant_id == tenant_id,
        )
    )
    utr = result.scalar_one_or_none()
    if not utr:
        raise HTTPException(404, "User-tenant assignment not found")
    await db.delete(utr)
    await db.commit()
    return {"success": True, "detail": f"User {user_id} removed from tenant {tenant_id}"}


# ============== Dataset 共享 ==============

@router.post("/datasets/{dataset_id}/share")
async def share_dataset(
    dataset_id: int,
    body: DatasetShare,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """共享 dataset 给指定用户 (v3.3.6 严格最小权限: 仅 owner 可共享).

    v3.3.6 修复 (admin 越权):
      - 旧逻辑: is_admin() 可共享任何 dataset, 包括别人的 (横向越权)
      - 新逻辑: 仅 owner 可共享 (与 team.py 保持一致, 严格最小权限)
      - super_admin 不再具备「代管共享」权限 (与 team.unshare_dataset 一致)

    业务背景:
      - 数据集 owner 是数据责任人, 共享决定权应归 owner
      - regular admin 通过此端点可破坏别人协作关系 (横向越权)
      - 业务上"代管"场景由 owner 主动 + super_admin 通过迁移脚本处理
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    # v3.3.6: 严格最小权限 — 仅 owner 可共享
    if dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限共享此数据集: 仅数据集 owner 可操作")

    if body.role not in DATASET_SHARE_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}")

    # 用户存在检查
    user = await db.get(User, body.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    # 幂等
    existing = await db.execute(
        select(DatasetMembership).where(
            DatasetMembership.dataset_id == dataset_id,
            DatasetMembership.user_id == body.user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Dataset already shared with this user")

    dm = DatasetMembership(
        dataset_id=dataset_id,
        user_id=body.user_id,
        role=body.role,
        assigned_by=current_user.id,
    )
    db.add(dm)
    await db.commit()
    return {
        "dataset_id": dm.dataset_id,
        "user_id": dm.user_id,
        "role": dm.role,
    }


@router.delete("/datasets/{dataset_id}/share/{user_id}")
async def unshare_dataset(
    dataset_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消共享 dataset (v3.3.6 严格最小权限: 仅 owner 可取消).

    v3.3.6 修复 (admin 越权):
      - 旧逻辑: is_admin() 可取消任何 dataset 的共享 (横向越权)
      - 新逻辑: 仅 owner 可取消 (与 team.unshare_dataset 策略保持一致)
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    # v3.3.6: 严格最小权限 — 仅 owner 可取消共享
    if dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限取消共享: 仅数据集 owner 可操作")

    result = await db.execute(
        select(DatasetMembership).where(
            DatasetMembership.dataset_id == dataset_id,
            DatasetMembership.user_id == user_id,
        )
    )
    dm = result.scalar_one_or_none()
    if not dm:
        raise HTTPException(404, "Dataset membership not found")
    await db.delete(dm)
    await db.commit()
    return {"success": True, "detail": f"Dataset {dataset_id} unshared from user {user_id}"}
