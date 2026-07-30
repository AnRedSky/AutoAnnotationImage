"""
UserService — 用户业务编排 (app/admin/service/)
==============================================

**职责**:
- 用户 CRUD 编排
- 角色 / 权限校验
- 账号启停

**v3.0.0 Stage 2.4 迁移**: 从 app/services/user_service.py 迁入 admin 应用
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.admin.repository.user_queries import (
    get_user_by_username,
    list_active_users,
    list_admins,
)

logger = logging.getLogger(__name__)


class UserService:
    """用户业务编排 (无状态, 静态方法)"""

    @staticmethod
    async def get(db: AsyncSession, user_id: int) -> Optional[User]:
        return await db.get(User, user_id)

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> Optional[User]:
        return await get_user_by_username(db, username)

    @staticmethod
    async def list_active(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[User]:
        # list_active_users 不支持 skip/limit, 直接返回全量 (admin 端点数据量小)
        return await list_active_users(db)

    @staticmethod
    async def count(db: AsyncSession) -> int:
        """统计用户数 (含未激活)"""
        from sqlalchemy import func
        result = await db.execute(select(func.count(User.id)))
        return result.scalar_one() or 0

    @staticmethod
    async def deactivate(db: AsyncSession, user: User) -> User:
        """停用账号 (业务规则: 管理员不可停用自己)"""
        # 此规则由调用方 (API 层) 校验, 这里只执行 ORM 业务方法
        user.deactivate()
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def activate(db: AsyncSession, user: User) -> User:
        user.activate()
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def change_role(
        db: AsyncSession,
        user: User,
        new_role: str,
    ) -> User:
        """修改用户角色 (业务规则: 角色必须合法)"""
        valid_roles = ("admin", "annotator", "viewer")
        if new_role not in valid_roles:
            raise HTTPException(400, f"Invalid role: {new_role}, must be one of {valid_roles}")
        user.role = new_role
        await db.commit()
        await db.refresh(user)
        return user

    # ============== 业务校验 ==============

    @staticmethod
    def assert_can_modify(
        actor: User,
        target: User,
    ) -> None:
        """业务规则: 谁能修改谁

        - admin 可改任何人
        - 非 admin 只能改自己
        """
        if actor.is_admin():
            return
        if actor.id != target.id:
            raise HTTPException(403, "Permission denied: only admin can modify other users")


__all__ = ["UserService"]
