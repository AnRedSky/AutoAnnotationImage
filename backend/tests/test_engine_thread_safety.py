"""
Tests for v3.5.2 engine thread-safety improvements.

Covers the `RuntimeError: got Future ... attached to a different loop` bug
triggered when Celery `threads` pool with `worker_max_tasks_per_child=10`
restarts a worker thread and the OS reuses the tid.

Background
----------
v3.5.1 introduced thread-local engines keyed by `threading.get_ident()`.
That fix was incomplete because the OS reuses tids after a thread dies,
so a new thread can hit the stale cache. v3.5.2 adds liveness checks
on every `get_thread_engine()` call:

    Rule 1: entry.thread is threading.current_thread() (catches tid reuse)
    Rule 2: entry.thread.is_alive() (defensive)
    Rule 3: entry.loop is current_loop and not closed() (catches loop replace)

Multi-thread fan-out pattern is borrowed from
tests/test_thumbnail_cache.py:TestThumbnailCacheThreadSafety.
"""
import asyncio
import threading

import pytest

from app.database import AsyncSessionLocal
from app.database import engine as proxy_engine
from app.database.engine import (
    _ThreadEngineEntry,
    _create_thread_engine,
    _entries_lock,
    _is_entry_stale,
    _main_engine,
    _main_session_factory,
    _main_thread_ident,
    _thread_entries,
    _try_get_current_loop,
    dispose_thread_engine,
    get_thread_engine,
    get_thread_session_factory,
)


# ============== 1. Main thread regression ==============

class TestMainThreadRegression:
    """主线程必须永远返回 _main_engine (无 thread-local 开销)."""

    def test_main_thread_returns_main_engine(self):
        """主线程调 get_thread_engine() 必须返回 _main_engine 单例."""
        assert threading.get_ident() == _main_thread_ident
        assert get_thread_engine() is _main_engine

    def test_main_thread_returns_main_session_factory(self):
        """主线程调 get_thread_session_factory() 必须返回 _main_session_factory."""
        assert get_thread_session_factory() is _main_session_factory

    def test_main_thread_proxy_resolves_to_main_engine(self):
        """_ThreadAwareAlias proxy 访问 .dispose 应绑定到 _main_engine."""
        # proxy 本身不是 engine
        assert proxy_engine is not _main_engine
        # 但 proxy.dispose 应绑定到 _main_engine.dispose
        assert proxy_engine.dispose.__self__ is _main_engine


# ============== 2. Multi-thread independence ==============

class TestMultiThreadIndependence:
    """每个非主线程拿到独立 engine, 且都不等于主线程 engine."""

    def test_four_threads_get_distinct_engines(self):
        results: dict[str, object] = {}
        errors: list = []
        barrier = threading.Barrier(4)

        def worker(name: str) -> None:
            try:
                barrier.wait(timeout=5)
                results[name] = get_thread_engine()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"t{i}",))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, errors
        assert len(results) == 4, f"expected 4 results, got {len(results)}"
        engines = list(results.values())
        # 4 distinct engine instances
        assert len({id(e) for e in engines}) == 4, (
            f"expected 4 distinct engines, got {len({id(e) for e in engines})}"
        )
        # None of them is the main engine
        for eng in engines:
            assert eng is not _main_engine, (
                "worker thread should not get main engine"
            )


# ============== 3. Stale entry detection ==============

class TestStaleEntryDetection:
    """stale entry 必须被驱逐并重建 (Celery thread restart 场景)."""

    def test_swap_thread_ref_triggers_eviction(self):
        """手动把 entry.thread 换成假 Thread, 下次 get_thread_engine() 应返回新 engine.

        模拟场景: OS 复用了 tid, 但新 Thread 对象与缓存中的不同.
        """
        captured: dict = {}

        def worker() -> None:
            tid = threading.get_ident()
            eng1 = get_thread_engine()
            captured["eng1"] = eng1
            captured["tid"] = tid

            entry = _thread_entries[tid]
            assert entry.thread is threading.current_thread()

            # 模拟 tid 复用: 把 entry.thread 换成假 Thread
            fake_old = threading.Thread(
                target=lambda: None, name="fake-dead-thread",
            )
            entry.thread = fake_old

            # 规则 1 应触发 stale → 重建
            eng2 = get_thread_engine()
            captured["eng2"] = eng2

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert captured["eng1"] is not captured["eng2"], (
            "stale entry was not evicted; new call returned the same engine"
        )

    def test_closed_loop_marks_entry_stale(self):
        """如果 entry.loop 是已关闭的 loop, 应被识别为 stale.

        模拟场景: dispose_worker_loop 关闭了 loop, 但 entry 仍在缓存里.

        注意事项: _get_worker_loop 必须在 worker 线程里 prime, 才能让
        entry.loop 被设置. 否则 entry.loop 为 None, 规则 3 不触发.
        """
        captured: dict = {}

        def worker() -> None:
            # 走 _get_worker_loop 让 entry.loop 正确初始化
            from app.utils.async_helpers import _get_worker_loop
            _get_worker_loop()

            eng1 = get_thread_engine()
            captured["eng1"] = eng1

            entry = _thread_entries[threading.get_ident()]
            assert entry.loop is not None, (
                "_get_worker_loop should have set entry.loop"
            )

            # 替换 entry.loop 为已关闭的 loop
            fake_loop = asyncio.new_event_loop()
            fake_loop.close()
            entry.loop = fake_loop

            eng2 = get_thread_engine()
            captured["eng2"] = eng2

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert captured["eng1"] is not captured["eng2"], (
            "closed loop did not trigger stale eviction"
        )

    def test_dead_thread_via_is_alive_triggers_eviction(self):
        """entry.thread.is_alive() == False → stale (规则 2)."""
        captured: dict = {}

        def worker() -> None:
            eng1 = get_thread_engine()
            captured["eng1"] = eng1

            entry = _thread_entries[threading.get_ident()]
            assert entry.thread is threading.current_thread()
            # 模拟规则 2: 把 is_alive() patch 成 False
            entry.thread.is_alive = lambda: False  # type: ignore[method-assign]

            eng2 = get_thread_engine()
            captured["eng2"] = eng2

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert captured["eng1"] is not captured["eng2"], (
            "is_alive() == False did not trigger stale eviction"
        )


# ============== 4. Dispose + recreate ==============

class TestDisposeAndRecreate:
    """dispose_thread_engine 后, 下次调用应创建新 engine."""

    def test_dispose_then_get_yields_new_engine(self):
        captured: dict = {}

        def worker() -> None:
            # 走 _get_worker_loop 让 entry.loop 正确初始化
            from app.utils.async_helpers import _get_worker_loop
            _get_worker_loop()

            eng1 = get_thread_engine()
            captured["eng1"] = eng1
            tid = threading.get_ident()
            # dispose_thread_engine 是 async, 用 asyncio.run 驱动
            asyncio.run(dispose_thread_engine(tid))
            eng2 = get_thread_engine()
            captured["eng2"] = eng2

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert captured["eng1"] is not captured["eng2"], (
            "after dispose, get_thread_engine should create a new engine"
        )

    def test_dispose_removes_entry_from_dict(self):
        """dispose_thread_engine 应从 _thread_entries 中删除 entry."""
        captured: dict = {}

        def worker() -> None:
            from app.utils.async_helpers import _get_worker_loop
            _get_worker_loop()
            get_thread_engine()
            tid = threading.get_ident()
            captured["before"] = tid in _thread_entries
            asyncio.run(dispose_thread_engine(tid))
            captured["after"] = tid in _thread_entries

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert captured["before"] is True
        assert captured["after"] is False


# ============== 5. SQLite :memory: StaticPool preserved ==============

class TestSQLiteStaticPoolPreserved:
    """测试场景下 :memory: 必须保留 StaticPool (conftest 设的 sqlite+aiosqlite:///:memory: 环境下运行)."""

    def test_memory_engine_uses_static_pool(self):
        captured: dict = {}

        def worker() -> None:
            eng = get_thread_engine()
            captured["pool"] = type(eng.sync_engine.pool).__name__

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert captured["pool"] == "StaticPool", (
            f"expected StaticPool, got {captured.get('pool')}"
        )

    def test_main_thread_main_engine_also_uses_static_pool(self):
        """主线程的 _main_engine 同样应走 StaticPool (测试场景)."""
        assert type(_main_engine.sync_engine.pool).__name__ == "StaticPool"


# ============== 6. _ThreadAwareAlias proxy ==============

class TestThreadAwareAliasProxy:
    """_ThreadAwareAlias 必须在 thread 切换时仍正确解析."""

    def test_proxy_attribute_access_in_main_thread(self):
        # 主线程: 通过 proxy 拿到的 dispose 绑定在 _main_engine 上
        assert proxy_engine.dispose.__self__ is _main_engine

    def test_proxy_callable_in_worker_thread(self):
        """_ThreadAwareAlias() 在 worker 线程里应能正常返回 session_factory."""
        captured: dict = {}

        def worker() -> None:
            # AsyncSessionLocal 是 _ThreadAwareAlias 实例, __call__ 调用
            # _resolve_session_factory() 拿 session_factory, 然后再 () 创建 session
            fac = get_thread_session_factory()  # 拿 factory (不调 __call__)
            captured["factory_type"] = type(fac).__name__
            # 然后通过 proxy 调一次, 应能拿到 session
            captured["proxy_call_works"] = True
            try:
                sess = AsyncSessionLocal()
                # session 应有 async interface
                captured["has_execute"] = hasattr(sess, "execute")
                captured["has_commit"] = hasattr(sess, "commit")
            except Exception as e:  # noqa: BLE001
                captured["proxy_call_works"] = False
                captured["error"] = repr(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert captured["factory_type"].startswith("async_sessionmaker") or \
               "AsyncSession" in captured["factory_type"], (
                   f"unexpected factory type: {captured.get('factory_type')}"
               )
        assert captured.get("proxy_call_works") is True, (
            f"AsyncSessionLocal() failed in worker thread: {captured.get('error')}"
        )
        assert captured.get("has_execute") is True
        assert captured.get("has_commit") is True

    def test_proxy_engine_differs_between_threads(self):
        main_target = proxy_engine.dispose.__self__
        worker_target: list = []

        def worker() -> None:
            worker_target.append(proxy_engine.dispose.__self__)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert main_target is _main_engine
        assert worker_target[0] is not _main_engine
        assert worker_target[0] is not main_target


# ============== 7. Internal helpers sanity ==============

class TestInternalHelpers:
    """_is_entry_stale / _try_get_current_loop / _create_thread_engine 单测."""

    def test_is_entry_stale_returns_false_for_current_thread(self):
        """当前线程创建的 entry, 应判定为非 stale."""
        entry = _ThreadEngineEntry(
            engine=_main_engine,
            session_factory=_main_session_factory,
            thread=threading.current_thread(),
        )
        assert _is_entry_stale(entry) is False

    def test_is_entry_stale_returns_true_for_different_thread(self):
        """不同 Thread 对象应判定为 stale (规则 1)."""
        other = threading.Thread(target=lambda: None, name="other")
        entry = _ThreadEngineEntry(
            engine=_main_engine,
            session_factory=_main_session_factory,
            thread=other,
        )
        assert _is_entry_stale(entry) is True

    def test_is_entry_stale_loop_none_is_not_stale(self):
        """entry.loop 为 None 时, 规则 3 不触发, 不应误报 stale."""
        entry = _ThreadEngineEntry(
            engine=_main_engine,
            session_factory=_main_session_factory,
            thread=threading.current_thread(),
            loop=None,
        )
        assert _is_entry_stale(entry) is False

    def test_try_get_current_loop_returns_loop_when_primed(self):
        """若 _get_worker_loop 已 priming, _try_get_current_loop 应返回 loop."""

        captured: dict = {}

        def worker() -> None:
            from app.utils.async_helpers import _get_worker_loop
            _get_worker_loop()
            captured["loop"] = _try_get_current_loop()

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=15)

        assert captured["loop"] is not None
        assert isinstance(captured["loop"], asyncio.AbstractEventLoop)

    def test_create_thread_engine_returns_asyncengine(self):
        """_create_thread_engine 应返回 AsyncEngine 实例."""
        eng = _create_thread_engine()
        try:
            assert eng is not None
            # 验证 sync_engine 存在
            assert eng.sync_engine is not None
        finally:
            # 清理: 别让测试 engine 留在 _thread_entries 里
            tid = threading.get_ident()
            _thread_entries.pop(tid, None)
