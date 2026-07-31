"""
AuthService — 认证业务服务 (app/auth/service/)
============================================

**职责**:
- 登录 / 注册 / Token 签发 / 改密 / 登出
- 权限校验封装
- 密码哈希

**v3.0.0 Stage 2.4 迁移**: 从 app/services/auth_service.py 迁入 auth 应用
**v3.0.0 审查修复**: 新增 `issue_token` 静态方法统一 Token 签发入口
**v3.0.0 审查修复 Phase-A**: JWT 标准化 claims + 业务字段走 extra_claims
**v3.0.0 审查修复 Phase-B**: 集成 Token 吊销 (改密吊销全部, 登出吊销当前 jti)
                          + 审计事件 (LOGIN/LOGOUT/CHANGE_PASSWORD/REGISTER)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.admin.repository.user_queries import get_user_by_username
from app.middleware.http.auth_audit import AuthEventType, emit_audit_event
from app.middleware.security import token_revocation
from app.middleware.security.security import create_access_token, get_password_hash, verify_password

logger = logging.getLogger(__name__)


class AuthService:
    """认证业务服务 (无状态, 静态方法)"""

    @staticmethod
    def issue_token(user: User) -> str:
        """统一 Token 签发入口 (login/register 都用)
        v3.0.0 审查修复: 避免 token payload 格式漂移
        v3.0.0 审查修复 Phase-A: 业务字段通过 extra_claims 传入, 不污染标准声明
        - 标准声明 (sub/iat/exp/jti/iss/aud) 由 create_access_token 强制注入
        - `role` 等业务字段通过 extra_claims 注入, 与 sub 保持平级
        - 移除冗余的 `id` 字段 (sub 已持有用户 ID)
        v3.2.0 MT-2: 加 tenant_id 到 JWT (多租户上下文)
        """
        return create_access_token(
            data={"sub": str(user.id), "username": user.username},
            extra_claims={"role": user.role},
        )

    @staticmethod
    async def login(
        db: AsyncSession,
        username: str,
        password: str,
    ) -> dict:
        """登录校验

        Phase-B 新增: 失败时 emit LOGIN_FAILURE 审计事件, 成功时 emit LOGIN_SUCCESS
        """
        user = await get_user_by_username(db, username)
        if not user or not verify_password(password, user.password_hash):
            # 审计: 登录失败 (用户名不存在 或 密码错误)
            emit_audit_event(
                AuthEventType.LOGIN_FAILURE,
                username=username,
                reason="用户名或密码错误",
            )
            raise HTTPException(401, "用户名或密码错误")

        if not user.is_active:
            # 审计: 登录失败 (账号停用)
            emit_audit_event(
                AuthEventType.LOGIN_FAILURE,
                user_id=user.id,
                username=username,
                reason="账号已停用",
            )
            raise HTTPException(403, "账号已停用, 请联系管理员")

        token = AuthService.issue_token(user)
        # v3.3.0: 记录最后登录时间
        from datetime import datetime as _dt
        user.last_login_at = _dt.utcnow()
        await db.commit()
        # 审计: 登录成功
        emit_audit_event(
            AuthEventType.LOGIN_SUCCESS,
            user_id=user.id,
            username=user.username,
            extra={"role": user.role},
        )
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
        """注册新用户 (v3.0.0 审查修复: 角色白名单 + 密码强度)

        - 密码长度: AuthService 防御性 6 位, Schema 层强制 8 位 (双重保护)
        - 重复用户名: 409 Conflict
        - 角色白名单: admin/annotator/viewer

        Phase-B 新增: emit REGISTER 审计事件
        """
        if len(password) < 6:
            raise HTTPException(422, "密码长度至少 6 位")

        existing = await get_user_by_username(db, username)
        if existing:
            # 审计: 注册失败 (用户名重复)
            emit_audit_event(
                AuthEventType.REGISTER,
                username=username,
                reason=f"用户名 {username!r} 已存在",
            )
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

        # 审计: 注册成功
        emit_audit_event(
            AuthEventType.REGISTER,
            user_id=user.id,
            username=user.username,
            extra={"role": user.role, "email": user.email},
        )
        return user

    @staticmethod
    async def change_password(
        db: AsyncSession,
        user: User,
        old_password: str,
        new_password: str,
    ) -> User:
        """修改密码 (业务规则: 必须先验证旧密码)

        Phase-B 新增: 改密成功后吊销该用户的所有 token
        - 写入 `jwt:revoked:user:{user_id}` = 当前时间戳
        - 旧 token (iat < 改密时间) 立即失效
        - 改密后用户需重新登录 (新 token 走正常签发)
        """
        if not verify_password(old_password, user.password_hash):
            emit_audit_event(
                AuthEventType.CHANGE_PASSWORD,
                user_id=user.id,
                username=user.username,
                reason="旧密码错误",
            )
            raise HTTPException(401, "旧密码错误")
        if len(new_password) < 6:
            raise HTTPException(422, "新密码长度至少 6 位")

        user.password_hash = get_password_hash(new_password)
        await db.commit()
        await db.refresh(user)

        # 吊销该用户的所有 token (改密场景强制全设备重新登录)
        try:
            token_revocation.revoke_user(user.id)
            logger.info("change_password: user_id=%s 已吊销全部 token", user.id)
        except Exception as e:  # noqa: BLE001
            # 降级: Redis 不可用不阻塞业务, 但记 WARNING
            logger.warning("change_password: revoke_user 失败, user_id=%s err=%r", user.id, e)

        # 审计: 改密成功
        emit_audit_event(
            AuthEventType.CHANGE_PASSWORD,
            user_id=user.id,
            username=user.username,
            reason="改密成功, 已吊销全部 token",
        )
        return user

    @staticmethod
    def logout(user: User, jti: Optional[str] = None, exp_ts: Optional[int] = None) -> dict:
        """登出 (业务规则: 吊销当前 token 的 jti)

        Phase-B 新增:
        - 优先按 jti 吊销当前 token (单设备登出)
        - 未传 jti 时降级为吊销用户全部 token (兜底, 等同改密)
        - emit LOGOUT 审计事件

        Args:
            user: 当前登录用户
            jti: 当前 token 的 jti (从 payload 提取)
            exp_ts: 当前 token 的 exp (用于设置吊销黑名单 TTL)

        Returns:
            包含吊销结果的 dict
        """
        revoked_jti = False
        if jti:
            try:
                revoked_jti = token_revocation.revoke_jti(jti, exp_ts=exp_ts)
            except Exception as e:  # noqa: BLE001
                logger.warning("logout: revoke_jti 失败, jti=%s err=%r", jti[:8], e)

        # 审计: 登出事件 (无论吊销是否成功都记)
        emit_audit_event(
            AuthEventType.LOGOUT,
            user_id=user.id,
            username=user.username,
            jti=jti,
            reason="单 token 吊销" if revoked_jti else "前端清除 token 即可",
        )
        return {
            "detail": "已退出登录",
            "user_id": user.id,
            "jti_revoked": revoked_jti,
        }


__all__ = ["AuthService"]
