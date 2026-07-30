"""
Tests for thumbnail cache (RED for Phase V optimization)

BACKGROUND (docs/38 §六 + Phase V):
  当前 ``GET /api/files/{image_id}/thumbnail`` 每次都 PIL 重编码同一张图。
  缩略图接口 P95 大; 一张图被 N 用户访问 = N 次重算。

本测试针对独立 cache 层 ``app/common/cache/thumbnail_cache.py``:
  (真正的端点集成测试 - 验证缓存存在 + 命中/未命中正确).

RED — 当前没有 thumbnail_cache module; 测试应该失败 (ImportError / AttributeError).
"""
import pytest


class TestThumbnailCacheModule:
    """验证 thumbnail_cache 模块存在 + 提供正确接口."""

    def test_module_imports(self):
        """模块能 import"""
        from app.common.cache import thumbnail_cache  # noqa: F401

    def test_module_exposes_get_function(self):
        """导出 thumbnail_for() 函数"""
        from app.common.cache.thumbnail_cache import thumbnail_for
        assert callable(thumbnail_for)

    def test_module_exposes_cache_clear(self):
        """导出 clear_thumbnail_cache() 用于测试隔离"""
        from app.common.cache.thumbnail_cache import clear_thumbnail_cache
        assert callable(clear_thumbnail_cache)


class TestThumbnailCacheSemantics:
    """功能契约: 第一次算, 第二次命中."""

    def test_first_call_invokes_loader(self, monkeypatch):
        """首次调用: loader 被调一次"""
        from app.common.cache import thumbnail_cache

        call_count = {"n": 0}

        def fake_loader(image_id, size):
            call_count["n"] += 1
            return f"bytes-for-{image_id}-at-{size}".encode("utf-8")

        thumbnail_cache.clear_thumbnail_cache()
        result = thumbnail_cache.thumbnail_for(42, 240, loader=fake_loader)
        assert result == b"bytes-for-42-at-240"
        assert call_count["n"] == 1, f"loader 应该只被调一次, 实际调 {call_count['n']} 次"

    def test_second_call_uses_cache(self, monkeypatch):
        """第二次 (同 image_id+size): loader 不被调"""
        from app.common.cache import thumbnail_cache

        call_count = {"n": 0}

        def fake_loader(image_id, size):
            call_count["n"] += 1
            return f"v{call_count['n']}".encode()

        thumbnail_cache.clear_thumbnail_cache()
        b1 = thumbnail_cache.thumbnail_for(7, 240, loader=fake_loader)
        b2 = thumbnail_cache.thumbnail_for(7, 240, loader=fake_loader)
        b3 = thumbnail_cache.thumbnail_for(7, 240, loader=fake_loader)

        assert b1 == b2 == b3
        assert call_count["n"] == 1, f"应该缓存命中, 实际 loader 被调 {call_count['n']} 次"

    def test_different_sizes_separately_cached(self):
        """不同 size 是不同的 cache key: size=240 vs size=480 各算一次"""
        from app.common.cache import thumbnail_cache

        call_count = {"n": 0}

        def fake_loader(image_id, size):
            call_count["n"] += 1
            return f"v{call_count['n']}-{size}".encode()

        thumbnail_cache.clear_thumbnail_cache()
        a240 = thumbnail_cache.thumbnail_for(99, 240, loader=fake_loader)
        a480 = thumbnail_cache.thumbnail_for(99, 480, loader=fake_loader)

        assert a240 != a480
        assert call_count["n"] == 2

    def test_different_images_separately_cached(self):
        """不同 image_id 独立 cache"""
        from app.common.cache import thumbnail_cache

        call_count = {"n": 0}

        def fake_loader(image_id, size):
            call_count["n"] += 1
            return f"img{image_id}".encode()

        thumbnail_cache.clear_thumbnail_cache()
        a = thumbnail_cache.thumbnail_for(1, 240, loader=fake_loader)
        b = thumbnail_cache.thumbnail_for(2, 240, loader=fake_loader)
        a2 = thumbnail_cache.thumbnail_for(1, 240, loader=fake_loader)

        assert a != b
        assert a == a2
        assert call_count["n"] == 2

    def test_bounded_by_maxsize(self, monkeypatch):
        """LRU 上限: cache 不能爆涨"""
        from app.common.cache import thumbnail_cache

        thumbnail_cache.clear_thumbnail_cache()
        # 用一些极限值调 max
        for i in range(2000):
            thumbnail_cache.thumbnail_for(i, 240, loader=lambda i=i, size=240: b"x"*10)
        # 检查内部 cache size 不超过约定上限
        internal = getattr(thumbnail_cache, "_cache", {})
        assert len(internal) <= thumbnail_cache.MAX_CACHE_ENTRIES, (
            f"cache 不应超过 {thumbnail_cache.MAX_CACHE_ENTRIES} entries, "
            f"实际 {len(internal)}"
        )


class TestThumbnailCacheThreadSafety:
    """FastAPI 是多线程的; cache 必须 thread-safe."""

    def test_concurrent_calls_only_load_once(self):
        """20 线程并发同 (image_id, size): loader 只被调 1 次 (其他都缓存命中)"""
        from app.common.cache import thumbnail_cache
        import threading

        call_count = {"n": 0}
        lock = threading.Lock()

        def fake_loader(image_id, size):
            with lock:
                call_count["n"] += 1
            return f"v{call_count['n']}".encode()

        thumbnail_cache.clear_thumbnail_cache()

        results = []
        results_lock = threading.Lock()

        def hit():
            r = thumbnail_cache.thumbnail_for(100, 240, loader=fake_loader)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=hit) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # loader 调了 >= 1 次 (理想 = 1), 但 <= N (允许 race 但不爆)
        assert call_count["n"] >= 1
        assert call_count["n"] <= 5, (
            f"20 线程并发, loader 被调 {call_count['n']} 次 (race 应当很小)"
        )
        # 所有结果应相等
        assert len(set(results)) == 1, (
            f"并发调用返回 {len(set(results))} 个不同结果, 应当只 1 个"
        )
