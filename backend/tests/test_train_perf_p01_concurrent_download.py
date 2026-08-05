"""
v3.6.0 P1 — MinIO 训练样本并发预下载测试
==========================================

**目标**:
- 验证 _download_one_to_cache 单条下载逻辑
- 验证 _download_batch_concurrent 信号量限流行为
- 验证错误注入 (单条失败不影响整体)
- 验证缓存命中 (local_path.exists() 时不重复下载)

**测试方法**:
- 通过 sys.modules 拿到 storage_service 模块 (因 storage_service 全局变量已被
  替换为 _LazyStorageProxy 实例, 直接 `from x import y` 拿不到模块本身)
- monkeypatch `app.common.storage.storage_service._real_storage_service` 函数
- 不依赖真实 MinIO,纯 in-process 验证逻辑正确性
- 用 asyncio.sleep 模拟 IO 延迟, 验证并发确实并行
- 计数: 验证最大并发数 ≤ 配置的 MINIO_DOWNLOAD_CONCURRENCY
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import List, Tuple

import pytest

# 测试环境: 必须在 import app 前设置
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("APP_DEBUG", "False")

from app.core.config import settings  # noqa: E402
from app.tasks.service.training_data_service import TrainingDataService  # noqa: E402


# ============== 辅助 Fixtures ==============

@pytest.fixture
def temp_cache_dir(monkeypatch, tmp_path):
    """临时缓存目录, 替换 settings.UPLOAD_DIR/_training_cache"""
    cache_dir = tmp_path / "_training_cache" / "1"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    return cache_dir


class _FakeStorage:
    """Mock storage backend (内存版) - 简化版 FakeStorage

    与原 _perf_p01_manual.py 的 FakeStorage 一致; 在 fixture 中实例化.
    """
    def __init__(self):
        self.call_count = 0
        self.concurrent_max = 0
        self.concurrent_current = 0
        self._lock = None
        self.fail_keys: set = set()
        self.delay: float = 0.05
        self.exist_keys: set = set()

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def load(self, key: str) -> bytes:
        lock = self._get_lock()
        async with lock:
            self.concurrent_current += 1
            self.concurrent_max = max(self.concurrent_max, self.concurrent_current)
            self.call_count += 1
        try:
            if key in self.fail_keys:
                raise RuntimeError(f"fake load failure: {key}")
            await asyncio.sleep(self.delay)
            return f"content-of-{key}".encode("utf-8")
        finally:
            async with lock:
                self.concurrent_current -= 1

    def exists(self, key: str) -> bool:
        return key in self.exist_keys


@pytest.fixture
def fake_storage(monkeypatch):
    """伪造 storage_service backend

    v3.6.0 测试策略: patch `app.common.storage.storage_service._real_storage_service`
    让其返回 _FakeStorage 实例. _LazyStorageProxy 每次访问属性都调 _real_storage_service(),
    所以 fake.load / fake.exists 会被代理透明路由到这里.
    """
    fake = _FakeStorage()
    # 通过 sys.modules 拿真实模块 (因 `from app.common.storage import storage_service`
    # 拿到的是 _LazyStorageProxy 实例, 不是模块)
    _ss_mod = sys.modules["app.common.storage.storage_service"]
    monkeypatch.setattr(_ss_mod, "_real_storage_service", lambda: fake)
    return fake


# ============== T1.1: 单条下载 (缓存命中 + 实际下载) ==============

@pytest.mark.asyncio
async def test_t1_1_download_one_cache_hit(temp_cache_dir, fake_storage):
    """缓存命中: local_path 已存在, 不调用 storage.load"""
    local_path = temp_cache_dir / "cached.png"
    local_path.write_bytes(b"already here")
    fake_storage.exist_keys.add("ds/1/foo.png")

    ok = await TrainingDataService._download_one_to_cache(
        "ds/1/foo.png", local_path,
    )
    assert ok is True
    # 缓存命中不调用 load
    assert fake_storage.call_count == 0
    # 文件未被覆盖
    assert local_path.read_bytes() == b"already here"


@pytest.mark.asyncio
async def test_t1_1_download_one_actual_download(temp_cache_dir, fake_storage):
    """实际下载: local_path 不存在, 调用 load 并写文件"""
    local_path = temp_cache_dir / "new.png"
    assert not local_path.exists()
    fake_storage.exist_keys.add("ds/1/new.png")

    ok = await TrainingDataService._download_one_to_cache(
        "ds/1/new.png", local_path,
    )
    assert ok is True
    assert fake_storage.call_count == 1
    assert local_path.exists()
    assert local_path.read_bytes() == b"content-of-ds/1/new.png"


# ============== T1.3: 单条失败不抛异常 ==============

@pytest.mark.asyncio
async def test_t1_3_download_one_failure_silent(temp_cache_dir, fake_storage):
    """错误注入: load 抛异常 → 返回 False, 不抛"""
    local_path = temp_cache_dir / "fail.png"
    fake_storage.fail_keys.add("ds/1/fail.png")

    ok = await TrainingDataService._download_one_to_cache(
        "ds/1/fail.png", local_path,
    )
    assert ok is False
    assert not local_path.exists()


# ============== T1.2: 并发下载 vs 串行下载 速度对比 ==============

@pytest.mark.asyncio
async def test_t1_2_concurrent_faster_than_serial(temp_cache_dir, fake_storage):
    """10 并发下载应显著快于串行 (50ms × 5 = 250ms → 50ms+overhead)"""
    fake_storage.delay = 0.05
    n = 5

    # 串行测试
    serial_dir = temp_cache_dir.parent / "serial_test" / "1"
    serial_dir.mkdir(parents=True, exist_ok=True)
    for f in serial_dir.glob("*.png"):
        f.unlink()
    serial_tasks = [
        (f"ds/1/ser_img_{i}.png", serial_dir / f"ser_img_{i}.png")
        for i in range(n)
    ]
    t0 = time.perf_counter()
    serial_results = await TrainingDataService._download_batch_concurrent(
        serial_tasks, concurrency=1,
    )
    serial_time = time.perf_counter() - t0

    # 并发测试 (新目录, 新 keys)
    concurrent_dir = temp_cache_dir.parent / "concurrent_test" / "1"
    concurrent_dir.mkdir(parents=True, exist_ok=True)
    for f in concurrent_dir.glob("*.png"):
        f.unlink()
    concurrent_tasks = [
        (f"ds/1/con_img_{i}.png", concurrent_dir / f"con_img_{i}.png")
        for i in range(n)
    ]
    fake_storage.concurrent_max = 0
    t0 = time.perf_counter()
    concurrent_results = await TrainingDataService._download_batch_concurrent(
        concurrent_tasks, concurrency=10,
    )
    concurrent_time = time.perf_counter() - t0

    assert all(r is True for r in serial_results)
    assert all(r is True for r in concurrent_results)
    # 串行时 max=1, 并发时 max>=2
    assert fake_storage.concurrent_max >= 2, (
        f"并发峰值应为 >= 2, 实际 {fake_storage.concurrent_max}"
    )
    # 并发应比串行快 (50ms vs 250ms, 至少快 1.5x)
    assert concurrent_time < serial_time * 0.7, (
        f"并发应至少快 30% 但实际: serial={serial_time:.3f}s concurrent={concurrent_time:.3f}s"
    )


# ============== T1.4: 信号量限流 ==============

@pytest.mark.asyncio
async def test_t1_4_semaphore_respects_concurrency_limit(temp_cache_dir, fake_storage):
    """信号量严格限流: 配置 concurrency=3, 实际并发峰值 ≤ 3"""
    fake_storage.delay = 0.1
    n = 10
    concurrency = 3

    fresh = temp_cache_dir.parent / "sem_test" / "1"
    fresh.mkdir(parents=True, exist_ok=True)
    for f in fresh.glob("*.png"):
        f.unlink()

    tasks = [
        (f"ds/1/sem_{i}.png", fresh / f"sem_{i}.png")
        for i in range(n)
    ]

    fake_storage.concurrent_max = 0
    results = await TrainingDataService._download_batch_concurrent(
        tasks, concurrency=concurrency,
    )
    assert all(r is True for r in results)
    # 关键断言: 最大并发严格 ≤ 配置的 concurrency
    assert fake_storage.concurrent_max <= concurrency, (
        f"信号量应限制最大并发为 {concurrency}, 实际峰值 {fake_storage.concurrent_max}"
    )
    # 同时验证确实并行 (不是 1)
    assert fake_storage.concurrent_max >= 2


# ============== T1.5: 缓存命中验证 ==============

@pytest.mark.asyncio
async def test_t1_5_cache_hit_skips_download(temp_cache_dir, fake_storage):
    """缓存命中: local_path 已存在, 整批 batch 不调用 load"""
    n = 5
    cached = []
    tasks = []
    for i in range(n):
        lp = temp_cache_dir / f"cached_{i}.png"
        lp.write_bytes(f"old-{i}".encode())
        tasks.append((f"ds/1/c_{i}.png", lp))
        cached.append(lp)

    fake_storage.call_count = 0
    results = await TrainingDataService._download_batch_concurrent(
        tasks, concurrency=10,
    )
    assert all(r is True for r in results)
    # 关键断言: 全部缓存命中, 0 次 load 调用
    assert fake_storage.call_count == 0, (
        f"缓存命中应 0 次 load, 实际 {fake_storage.call_count}"
    )
    # 文件内容未被覆盖
    for i, lp in enumerate(cached):
        assert lp.read_bytes() == f"old-{i}".encode()


# ============== T1.6: 串行路径兼容 (concurrency=1) ==============

@pytest.mark.asyncio
async def test_t1_6_serial_path_compat(temp_cache_dir, fake_storage):
    """concurrency=1 走串行路径, 等价于旧 for 循环"""
    fake_storage.delay = 0.01
    n = 4
    fresh = temp_cache_dir.parent / "ser_compat_test" / "1"
    fresh.mkdir(parents=True, exist_ok=True)
    for f in fresh.glob("*.png"):
        f.unlink()

    tasks = [
        (f"ds/1/ser_{i}.png", fresh / f"ser_{i}.png")
        for i in range(n)
    ]

    fake_storage.concurrent_max = 0
    results = await TrainingDataService._download_batch_concurrent(
        tasks, concurrency=1,
    )
    assert all(r is True for r in results)
    # 串行时峰值应为 1
    assert fake_storage.concurrent_max == 1
    for sp, lp in tasks:
        assert lp.exists()
        assert lp.read_bytes() == f"content-of-{sp}".encode("utf-8")


# ============== T1.7: 错误注入 — 单条失败不抛 ==============

@pytest.mark.asyncio
async def test_t1_7_single_failure_does_not_break_batch(temp_cache_dir, fake_storage):
    """batch 中第 3 条失败, 其他仍成功, 整体返回 list 不抛"""
    fake_storage.delay = 0.01
    n = 5
    fresh = temp_cache_dir.parent / "err_test" / "1"
    fresh.mkdir(parents=True, exist_ok=True)
    for f in fresh.glob("*.png"):
        f.unlink()

    tasks = [
        (f"ds/1/err_{i}.png", fresh / f"err_{i}.png")
        for i in range(n)
    ]
    # 第 3 条 (索引 2) 失败
    fake_storage.fail_keys.add(tasks[2][0])

    results = await TrainingDataService._download_batch_concurrent(
        tasks, concurrency=10,
    )
    assert len(results) == 5
    for i, r in enumerate(results):
        if i == 2:
            assert r is False
        else:
            assert r is True
