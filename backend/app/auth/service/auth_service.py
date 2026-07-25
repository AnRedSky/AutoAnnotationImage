"""
AuthService — 认证业务服务 (app/auth/service/)
============================================

**职责**:
- 登录 / 注册 / Token 签发
- 权限校验封装
- 密码哈希

**v3.0.0 Stage 2.4 迁移**: 从 app/services/auth_service.py 迁入 auth 应用
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.admin.repository.user_queries import get_user_by_username
from app.core.security import create_access_token, get_password_hash, verify_password

logger = logging.getLogger(__name__)


class AuthService:
    """认证业务服务 (无状态, 静态方法)"""

    @staticmethod
    async def login(
        db: AsyncSession,
        username: str,
        password: str,
    ) -> dict:
        """登录校验"""
        user = await get_user_by_username(db, username)
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(401, "用户名或密码错误")

        if not user.is_active:
            raise HTTPException(403, "账号已停用, 请联系管理员")

        token = create_access_token({"sub": user.username, "id": user.id, "role": user.role})
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role,
            },
        }

    @staticmethod
    async def register(
        db: AsyncSession,
        username: str,
        password: str,
        email: Optional[str] = None,
        role: str = "annotator",
    ) -> User:
        """注册新用户"""
        if len(password) < 6:
            raise HTTPException(422, "密码长度至少 6 位")

        existing = await get_user_by_username(db, username)
        if existing:
            raise HTTPException(409, f"用户名 {username!r} 已存在")

        valid_roles = ("admin", "annotator", "viewer")
        if role not in valid_roles:
            raise HTTPException(400, f"Invalid role: {role}")

        user = User(
            username=username,
            password_hash=get_password_hash(password),
            email=email,
            role=role,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def change_password(
        db: AsyncSession,
        user: User,
        old_password: str,
        new_password: str,
    ) -> User:
        """修改密码 (业务规则: 必须先验证旧密码)"""
        if not verify_password(old_password, user.password_hash):
            raise HTTPException(401, "旧密码错误")
        if len(new_password) < 6:
            raise HTTPException(422, "新密码长度至少 6 位")

        user.password_hash = get_password_hash(new_password)
        await db.commit()
        await db.refresh(user)
        return user


__all__ = ["AuthService"]
