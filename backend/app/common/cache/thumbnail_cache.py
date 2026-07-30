"""
缩略图进程内 LRU 缓存 (Phase V optimization)

背景:
  ``/api/files/{image_id}/thumbnail`` 每次重编码同一张图, P95 大。
  docs/38 §六 与 docs/04 性能优化建议均列为高价值优化。

设计:
  - 进程内 LRU dict (memory cache, 无磁盘 IO, 无 Redis)
  - key = (image_id, size) tuple -> bytes
  - 上限 = MAX_CACHE_ENTRIES (FIFO eviction when full)
  - thread-safe via threading.Lock

局限 (Phase V 不解决):
  - 单进程 in-memory; 多 worker 部署时 (uvicorn --workers N) cache 命中率为 1/N
  - 热路径应改 Redis (Phase W). 但这次最大化单 worker 内的代码最小化.

为什么不用 Redis 作为这次实现:
  - Redis 路径增加 1 网络 roundtrip + serialize/deserialize (典型 1-3ms)
  - 进程内 LRU 是 ~microseconds (dict lookup)
  - "最大快" for 1 worker; 当前 uvicorn 还是单 worker (proc_3072bf6953af 实测)
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Optional


# 上限: 假设每条 50KB 平均, 2000 条 = 100MB. 单 worker 内存预算合理.
MAX_CACHE_ENTRIES = 2000

# 模块级单例. FastAPI 进程内全局共享.
_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_lock = threading.Lock()


def thumbnail_for(
    image_id: int,
    size: int,
    loader: Callable[[int, int], bytes],
) -> bytes:
    """读 thumbnail (cache 优先, miss 时调 loader).

    Args:
        image_id: 图片 ID
        size: 缩略图最长边像素
        loader: cache miss 时调, 必须返回 bytes

    Returns:
        thumbnail bytes
    """
    key = (int(image_id), int(size))
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            # LRU: move to end (most-recently-used)
            _cache.move_to_end(key)
            return hit

    # 调 loader (在锁外, 避免阻塞其它调用; 但 record_cached 加锁)
    data = loader(int(image_id), int(size))
    with _lock:
        # Double-check: 别的线程可能在我们 loader 期间已写入
        existing = _cache.get(key)
        if existing is not None:
            _cache.move_to_end(key)
            return existing
        _cache[key] = data
        # LRU 驱逐
        while len(_cache) > MAX_CACHE_ENTRIES:
            _cache.popitem(last=False)  # FIFO, but we move_to_end on hit, so this is LRU
        return data


def clear_thumbnail_cache() -> None:
    """清空 cache (仅用于测试). 不应在生产代码调用."""
    with _lock:
        _cache.clear()


# 测试可见 (但生产不应 import 它)
__all__ = ["thumbnail_for", "clear_thumbnail_cache", "MAX_CACHE_ENTRIES"]
