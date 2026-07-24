"""
User 查询函数 (Data Layer)
==========================

封装复杂 user 查询, 避免散落各处的 select 语句。
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.user import User


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """按 ID 查用户 (返回 None 表示不存在)"""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """按 username 查用户 (登录用)"""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def list_active_users(db: AsyncSession) -> list[User]:
    """列出所有活跃用户"""
    result = await db.execute(
        select(User).where(User.is_active == True).order_by(User.id.asc())  # noqa: E712
    )
    return list(result.scalars().all())


async def list_admins(db: AsyncSession) -> list[User]:
    """列出所有管理员"""
    result = await db.execute(
        select(User).where(User.role == "admin", User.is_active == True)  # noqa: E712
    )
    return list(result.scalars().all())
