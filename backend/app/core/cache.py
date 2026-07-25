"""
Cache Layer (Core Layer)
========================

Redis-backed 缓存层, 提供装饰器和上下文两种使用方式.

**Stage 5.2 新增**.

**使用方式**:
```python
# 1) 装饰器
from app.core.cache import cached

@cached("user:{user_id}", ttl=300)
async def get_user_profile(user_id: int) -> dict:
    return await db.query(...)

# 2) 直接调用
from app.core.cache import cache
await cache.get("datasets:list:page=1")
await cache.set("datasets:list:page=1", data, ttl=60)
await cache.delete("datasets:*")  # 模式删除

# 3) 失效便捷
from app.core.cache import invalidate
await invalidate("datasets:*", "categories:*")  # 多模式失效
```

**配置**:
- CACHE_ENABLED (env, default=True)
- CACHE_DEFAULT_TTL (env, default=300s)
- CACHE_KEY_PREFIX (env, default="app:")

**降级**: Redis 不可用时所有调用降级为 noop, 不影响业务.

v3.0.0 Stage 5.2
"""
import functools
import hashlib
import json
import logging
import time
from typing import Any, Callable, Optional

from app.core.config import settings
from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)


def _serialize(value: Any) -> str:
    """JSON 序列化 (datetime/np/Path 等通过 default=str 兜底)"""
    return json.dumps(value, default=str, ensure_ascii=False)


def _deserialize(raw: str) -> Any:
    """JSON 反序列化"""
    return json.loads(raw)


def _make_key(template: str, args: tuple, kwargs: dict) -> str:
    """根据模板和参数生成缓存 key

    支持占位符: {arg_name} 或 {arg_name.subkey}
    例: "user:{user_id}" + (123,) -> "user:123"
    """
    # 合并 args 和 kwargs 用于格式化
    fmt_dict = {}
    for i, a in enumerate(args):
        fmt_dict[f"arg{i}"] = a
    fmt_dict.update(kwargs)

    # 简化: 如果模板中只有 {arg0} 这样的占位符
    try:
        return template.format(**fmt_dict)
    except KeyError:
        # 回退: 用 hash(args + kwargs) 作为标识
        sig = hashlib.md5(
            json.dumps({"args": args, "kwargs": kwargs}, default=str).encode()
        ).hexdigest()[:16]
        return f"{template}:hash:{sig}"


class Cache:
    """Redis 缓存封装 (单例)

    提供 get/set/delete/clear 等基础方法, 以及 hit/miss 计数器.
    """

    _HIT_KEY = "cache:stats:hits"
    _MISS_KEY = "cache:stats:misses"

    def __init__(self):
        self.enabled = getattr(settings, "CACHE_ENABLED", True)
        self.default_ttl = int(getattr(settings, "CACHE_DEFAULT_TTL", 300))
        self.key_prefix = getattr(settings, "CACHE_KEY_PREFIX", "app:")
        self._redis = redis_client

    def _k(self, key: str) -> str:
        """加前缀的完整 key"""
        return f"{self.key_prefix}{key}"

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值 (无值或失败返回 None)"""
        if not self.enabled:
            return None
        try:
            raw = self._redis.get(self._k(key))
            if raw is None:
                self._redis.incr(self._HIT_KEY.replace("hits", "misses"))
                return None
            self._redis.incr(self._HIT_KEY)
            return _deserialize(raw)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Cache.get failed for {key}: {e!r}")
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """设置缓存值 (ttl 秒, None 用默认值)"""
        if not self.enabled:
            return False
        try:
            self._redis.setex(
                self._k(key),
                ttl or self.default_ttl,
                _serialize(value),
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Cache.set failed for {key}: {e!r}")
            return False

    def delete(self, key: str) -> bool:
        """删除指定 key"""
        if not self.enabled:
            return False
        try:
            self._redis.delete(self._k(key))
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Cache.delete failed for {key}: {e!r}")
            return False

    def delete_pattern(self, pattern: str) -> int:
        """按模式删除 (e.g. "datasets:*"), 返回删除数

        注: SCAN + DEL, 避免 KEYS 阻塞 Redis.
        """
        if not self.enabled:
            return 0
        full_pattern = self._k(pattern)
        try:
            deleted = 0
            for key in self._redis.scan_iter(match=full_pattern, count=100):
                self._redis.delete(key)
                deleted += 1
            return deleted
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Cache.delete_pattern failed for {pattern}: {e!r}")
            return 0

    def get_stats(self) -> dict:
        """获取缓存统计 (hit/miss 次数)"""
        try:
            hits = int(self._redis.get(self._HIT_KEY) or 0)
            misses = int(self._redis.get(self._MISS_KEY) or 0)
            total = hits + misses
            hit_rate = (hits / total * 100) if total > 0 else 0.0
            return {
                "hits": hits,
                "misses": misses,
                "total": total,
                "hit_rate_percent": round(hit_rate, 2),
            }
        except Exception:  # noqa: BLE001
            return {"hits": 0, "misses": 0, "total": 0, "hit_rate_percent": 0.0}

    def reset_stats(self) -> None:
        """重置统计 (主要用于测试)"""
        try:
            self._redis.delete(self._HIT_KEY, self._MISS_KEY)
        except Exception:  # noqa: BLE001
            pass


# 全局单例
cache = Cache()


def cached(key_template: str, ttl: Optional[int] = None):
    """缓存装饰器 (同步/异步函数通用)

    Args:
        key_template: key 模板, 支持 {arg_name} 占位符
        ttl: 过期秒数, None 用 cache.default_ttl

    Example:
        @cached("user:{user_id}", ttl=300)
        async def get_user_profile(user_id: int) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            key = _make_key(key_template, args, kwargs)
            hit = cache.get(key)
            if hit is not None:
                return hit
            # 缓存未命中, 执行原函数
            result = await func(*args, **kwargs)
            cache.set(key, result, ttl=ttl)
            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            key = _make_key(key_template, args, kwargs)
            hit = cache.get(key)
            if hit is not None:
                return hit
            result = func(*args, **kwargs)
            cache.set(key, result, ttl=ttl)
            return result

        # 依据函数类型返回对应 wrapper
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator


async def invalidate(*patterns: str) -> int:
    """异步失效一个或多个缓存模式 (便捷函数)

    Args:
        *patterns: 一个或多个 key 模式 (e.g. "datasets:*", "user:1")

    Returns:
        删除的 key 总数
    """
    total = 0
    for p in patterns:
        total += cache.delete_pattern(p)
    return total


def cache_get(key: str) -> Optional[Any]:
    """同步获取缓存 (便捷函数)"""
    return cache.get(key)


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """同步设置缓存 (便捷函数)"""
    return cache.set(key, value, ttl=ttl)


__all__ = ["cache", "cached", "invalidate", "cache_get", "cache_set", "Cache"]
