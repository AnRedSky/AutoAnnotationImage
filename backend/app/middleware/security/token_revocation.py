"""
Token 吊销服务 (Security Layer)
===============================

v3.0.0 Phase-B 新增: 基于 jti + Redis 的 token 吊销机制

**核心功能**:
- `revoke_jti(jti, ttl)`: 吊销单个 token (jti), TTL = 剩余有效期
- `revoke_user(user_id)`: 吊销某用户的所有 token (改密时用)
- `is_revoked(jti)`: 检查 jti 是否在黑名单中 (decode_token 集成)
- `is_user_revoked(user_id)`: 检查用户是否被全局吊销 (改密后)

**存储设计**:
- `jwt:revoked:jti:{jti}` → 1 (单 token 吊销, TTL = exp - now)
- `jwt:revoked:user:{user_id}` → unix_ts (用户级吊销, 后续签发的 token 必须 iat > 此时间戳)

**为什么用 jti 不用 sub?**:
- sub 是用户 ID, 多个设备登录会共享 sub
- jti 是单 token 唯一 ID, 吊销粒度到单个 token
- 用户级吊销用 `jwt:revoked:user:{user_id}` 记录时间戳, 后续签发时强制 iat > 时间戳

**降级**: Redis 不可用时返回 False (即"未吊销"), 业务可继续, 避免 Redis 故障导致全站登录失败
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from app.database.redis import redis_client

logger = logging.getLogger("app.middleware.security.revocation")

# ============== Key 前缀 (与 CACHE_KEY_PREFIX 区分) ==============
_PREFIX = "jwt:"


def _k_jti(jti: str) -> str:
    return f"{_PREFIX}revoked:jti:{jti}"


def _k_user(user_id: int | str) -> str:
    return f"{_PREFIX}revoked:user:{user_id}"


# ============== 单 Token 吊销 (按 jti) ==============

def revoke_jti(jti: str, *, exp_ts: Optional[int] = None, default_ttl: int = 86400) -> bool:
    """吊销单个 token (jti)

    Args:
        jti: JWT 唯一 ID
        exp_ts: token 过期时间 (Unix 秒), 用于设置黑名单 TTL
                传 None 时使用 default_ttl (24h)
        default_ttl: 兜底 TTL (秒), 默认 24h

    Returns:
        True=成功, False=Redis 失败 (降级)

    设计:
        - TTL = exp_ts - now (黑名单只在 token 有效期内有意义, 过期后自动清理)
        - 若 exp_ts 未传, 用 default_ttl (24h), 适合手动吊销场景
    """
    if not jti:
        logger.warning("revoke_jti: empty jti, skip")
        return False
    try:
        if exp_ts is not None:
            ttl = max(1, int(exp_ts) - int(time.time()))
        else:
            ttl = default_ttl
        redis_client.setex(_k_jti(jti), ttl, "1")
        logger.info("revoke_jti: jti=%s ttl=%ds", jti[:8], ttl)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("revoke_jti: redis failed, jti=%s err=%r", jti[:8], e)
        return False


def is_jti_revoked(jti: str) -> bool:
    """检查 jti 是否在黑名单中

    Returns:
        True=已吊销, False=未吊销 或 Redis 失败 (降级)
    """
    if not jti:
        return False
    try:
        return bool(redis_client.exists(_k_jti(jti)))
    except Exception as e:  # noqa: BLE001
        logger.warning("is_jti_revoked: redis failed, jti=%s err=%r", jti[:8], e)
        return False


# ============== 用户级吊销 (改密时用) ==============

def revoke_user(user_id: int | str) -> bool:
    """吊销某用户的所有 token (改密 / 封号 时用)

    原理:
        - 写入 `jwt:revoked:user:{user_id}` = 当前 Unix 时间戳
        - 后续签发的 token 强制 `iat > 时间戳`, 否则视为已吊销
        - 旧 token 的 jti 也会被单独吊销 (供 decode_token 检查)

    Returns:
        True=成功, False=Redis 失败
    """
    try:
        ts = int(time.time())
        # 设长 TTL (30 天), 覆盖所有可能的 token 有效期
        redis_client.setex(_k_user(user_id), 86400 * 30, str(ts))
        logger.info("revoke_user: user_id=%s ts=%d", user_id, ts)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("revoke_user: redis failed, user_id=%s err=%r", user_id, e)
        return False


def get_user_revoked_at(user_id: int | str) -> Optional[int]:
    """获取用户级吊销时间戳

    Returns:
        Unix 时间戳, None=未吊销 或 Redis 失败
    """
    try:
        raw = redis_client.get(_k_user(user_id))
        if raw is None:
            return None
        return int(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("get_user_revoked_at: redis failed, user_id=%s err=%r", user_id, e)
        return None


def is_user_revoked(user_id: int | str, iat: Optional[int] = None) -> bool:
    """检查 token 是否被用户级吊销

    Args:
        user_id: 用户 ID
        iat: token 签发时间 (Unix 秒), None=不检查时间戳

    Returns:
        True=已吊销, False=未吊销

    逻辑:
        - 若用户被吊销, 且 iat <= 吊销时间戳 → 已吊销
        - 若 iat > 吊销时间戳 → 视为吊销后签发的新 token, 放行
        - 适合"改密后, 用户重新登录, 新 token 通过; 旧 token 被拒"
    """
    revoked_at = get_user_revoked_at(user_id)
    if revoked_at is None:
        return False
    if iat is None:
        # 没传 iat 时, 只要用户被吊销过就视为吊销 (保守策略)
        return True
    return iat <= revoked_at


# ============== 综合检查 (供 decode_token 集成) ==============

def check_revoked(*, jti: Optional[str] = None, user_id: Optional[int | str] = None,
                  iat: Optional[int] = None) -> bool:
    """综合检查 token 是否被吊销

    Args:
        jti: 单 token 吊销检查
        user_id: 用户级吊销检查
        iat: token 签发时间 (用于用户级时间戳比较)

    Returns:
        True=已吊销, False=未吊销
    """
    if jti and is_jti_revoked(jti):
        return True
    if user_id is not None and is_user_revoked(user_id, iat):
        return True
    return False


__all__ = [
    "revoke_jti",
    "is_jti_revoked",
    "revoke_user",
    "get_user_revoked_at",
    "is_user_revoked",
    "check_revoked",
]
