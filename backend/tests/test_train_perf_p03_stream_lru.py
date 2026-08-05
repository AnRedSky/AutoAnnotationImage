"""
v3.6.0 P3 — MinIO 流式下载 + 图像预解码 LRU 测试
===============================================

**目标**:
- 验证 MinIO load_stream 按 1MB chunk 分块返回 (默认 chunk_size)
- 验证流式下载内存峰值 < 一次性 load
- 验证 StorageService 基类 load_stream 默认实现兼容
- 验证 _decode_image_cached 缓存命中 (同 path 二次访问 0 次 Image.open)
- 验证 LRU 容量限制 (maxsize=2 强制淘汰)

**测试方法**:
- mock MinIO 客户端的 get_object 返回一个 fake response (read/close/release_conn)
- 用 monkeypatch 注入 fake client
- 对 _decode_image_cached 用 monkeypatch + counter 跟踪 Image.open 调用次数
- LRU 容量限制直接调 lru_cache 函数, 验证 cache_info().currsize

**注意**:
- minio_storage_service 用 asyncio.to_thread 包装同步 SDK, 测试需用 pytest-asyncio
- 流式下载测试不实际下载, 纯 in-process mock
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

# 测试环境: 必须在 import app 前设置
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("APP_DEBUG", "False")


# ============== T3.1: MinIO load_stream 分块验证 ==============


class _FakeMinioResponse:
    """v3.6.0 P3 测试用: 模拟 minio SDK 的 Response 对象.

    - read(n): 阻塞读 n 字节, 返回 bytes
    - close(): 关闭
    - release_conn(): 释放连接

    通过 self._data (bytes) 提供数据, read 按 chunk_size 切片返回.
    """

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self.closed = False
        self.released = False

    def read(self, n: int) -> bytes:
        if self._pos >= len(self._data):
            return b""
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class _FakeMinioClient:
    """v3.6.0 P3 测试用: 模拟 minio.Minio 客户端.

    - get_object(bucket, key) 返回 _FakeMinioResponse
    - bucket_exists / make_bucket / remove_object / stat_object 占位
    """

    def __init__(self, store: dict = None):
        self._store = store or {}
        self.get_object_calls: list = []
        self.bucket_exists = lambda b: True
        self.make_bucket = lambda b: None
        self.remove_object = lambda b, k: None
        self.stat_object = lambda b, k: MagicMock()

    def get_object(self, bucket: str, key: str):
        self.get_object_calls.append((bucket, key))
        return _FakeMinioResponse(self._store[key])


@pytest.fixture
def fake_minio(monkeypatch):
    """注入 fake MinIO 客户端到 MinioStorageService 构造路径.

    策略: patch `app.common.storage.minio_storage_service.Minio`,
    让 MinioStorageService() 实例化时拿到 fake client, 跳过真实网络.
    """
    from app.common.storage import minio_storage_service as mod
    fake = _FakeMinioClient()
    monkeypatch.setattr(mod, "Minio", lambda *a, **kw: fake)
    return fake


def _populate_store(fake: _FakeMinioClient, n: int, size_bytes: int) -> List[str]:
    """往 fake store 写 n 个指定大小的 key, 返回 key 列表."""
    keys: List[str] = []
    for i in range(n):
        key = f"ds/1/img_{i}.png"
        # 5MB test data, 但只有前 1MB 是非零, 后 4MB 是 0 (验证 chunk 边界)
        data = b"\x01" * (size_bytes // 2) + b"\x00" * (size_bytes // 2)
        fake._store[key] = data
        keys.append(key)
    return keys


@pytest.mark.asyncio
async def test_t3_1_minio_load_stream_chunks(fake_minio):
    """MinIO load_stream 按 1MB chunk 分块返回, 5MB 对象 → 5 次 yield.

    验证:
    1. 默认 chunk_size=1MB (1<<20=1048576)
    2. 5MB 数据分 5 次 yield
    3. 每次 yield 字节数 ≤ chunk_size
    4. 累计字节数 == 原始数据大小
    """
    from app.common.storage.minio_storage_service import MinioStorageService

    # 5MB test object
    key = "ds/1/test_5mb.bin"
    fake_minio._store[key] = b"\xAB" * (5 * 1024 * 1024)

    svc = MinioStorageService()  # 用 fake_minio 替换的 Minio 构造
    chunks: List[bytes] = []
    async for chunk in svc.load_stream(key):
        chunks.append(chunk)

    # 5MB / 1MB = 5 chunks (最后一次可能不满 1MB)
    assert len(chunks) == 5, f"应 yield 5 次, 实际 {len(chunks)} 次"
    # 每块不超过 1MB
    for i, c in enumerate(chunks):
        assert len(c) <= 1024 * 1024, f"第 {i+1} 块超 1MB: {len(c)} bytes"
    # 总字节数等于原始
    assert sum(len(c) for c in chunks) == 5 * 1024 * 1024
    # 内容一致
    assert b"".join(chunks) == b"\xAB" * (5 * 1024 * 1024)
    # 资源释放
    # 注: 我们无法在 fake 跟踪 resp.close / release_conn 的精确调用, 但 get_object 应被调用 1 次
    assert len(fake_minio.get_object_calls) == 1


@pytest.mark.asyncio
async def test_t3_1_minio_load_stream_custom_chunk_size(fake_minio):
    """MinIO load_stream 自定义 chunk_size 生效.

    验证:
    1. chunk_size=2MB 时, 5MB → 3 chunks (2+2+1)
    2. chunk_size=512KB 时, 5MB → 10 chunks
    """
    from app.common.storage.minio_storage_service import MinioStorageService

    key = "ds/1/test_chunk.bin"
    fake_minio._store[key] = b"\xCD" * (5 * 1024 * 1024)

    svc = MinioStorageService()

    # 2MB chunks
    chunks_2m = []
    async for c in svc.load_stream(key, chunk_size=2 * 1024 * 1024):
        chunks_2m.append(c)
    assert len(chunks_2m) == 3
    assert all(len(c) <= 2 * 1024 * 1024 for c in chunks_2m)
    assert sum(len(c) for c in chunks_2m) == 5 * 1024 * 1024

    # 512KB chunks
    chunks_512k = []
    async for c in svc.load_stream(key, chunk_size=512 * 1024):
        chunks_512k.append(c)
    assert len(chunks_512k) == 10
    assert all(len(c) <= 512 * 1024 for c in chunks_512k)


@pytest.mark.asyncio
async def test_t3_2_minio_load_stream_memory_peak(fake_minio):
    """v3.6.0 P3: 流式下载内存峰值 < 一次性 load.

    验证策略:
    - 写入 10MB 对象到 fake store (避免 100MB 测试内存浪费)
    - 用 load_stream(1MB chunk) 流式拉, 跟踪单次 yield 字节最大值
    - 验证单次 yield 字节数 ≤ 1MB (1MB chunk + 一些开销容忍)
    - 反证: load() 会一次性返回 10MB, 内存峰值 10MB
    """
    from app.common.storage.minio_storage_service import MinioStorageService

    key = "ds/1/test_10mb.bin"
    fake_minio._store[key] = b"\x00" * (10 * 1024 * 1024)

    svc = MinioStorageService()
    max_chunk_size = 0
    total = 0
    async for c in svc.load_stream(key, chunk_size=1024 * 1024):
        max_chunk_size = max(max_chunk_size, len(c))
        total += len(c)
        # 主动释放引用
        del c

    # 1MB chunk (fake 不切分, 单次 yield = 全部 1MB; 真实 minio SDK 按 n 切)
    assert max_chunk_size <= 1024 * 1024, (
        f"单次 yield 字节数 {max_chunk_size} 超过 1MB chunk, 流式分块失效"
    )
    # 总数等于原始
    assert total == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_t3_2_minio_load_stream_does_not_buffer_all(fake_minio):
    """流式分块 vs 一次性 load 行为差异: 流式版应分多次 yield, 一次性版单次返回.

    这是 T3.2 关键断言: 流式版不会先攒齐再 yield (那样内存峰值 = 总大小).
    """
    from app.common.storage.minio_storage_service import MinioStorageService

    key = "ds/1/test_buffered.bin"
    fake_minio._store[key] = b"\xFF" * (10 * 1024 * 1024)  # 10MB

    svc = MinioStorageService()
    yield_count = 0
    async for c in svc.load_stream(key, chunk_size=1024 * 1024):
        yield_count += 1
    # 10MB / 1MB = 10 chunks (最后 1MB)
    assert yield_count == 10, f"应分 10 次 yield, 实际 {yield_count} 次 (说明 SDK 先 buffer 全量)"


# ============== T3.3: 基类 load_stream 默认实现 ==============


@pytest.mark.asyncio
async def test_t3_3_storage_service_default_load_stream(tmp_path, monkeypatch):
    """StorageService 基类 load_stream 默认实现: yield 一次 (单 chunk).

    默认实现是 yield self.load(key) 一次, 假装流式.
    行为: 拿到 1 个 chunk, 字节数 = 文件大小.
    """
    from app.common.storage.storage_service import StorageService
    from app.core.config import settings

    # 把 tmp_path 作为 base_dir (UPLOAD_DIR), 避免跨盘符的 _full_path 路径校验失败
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    # 写一个 3MB 文件
    big = tmp_path / "default.bin"
    big.write_bytes(b"\x42" * (3 * 1024 * 1024))

    svc = StorageService()
    chunks: List[bytes] = []
    async for chunk in svc.load_stream("default.bin"):
        chunks.append(chunk)

    # 默认实现 = 单次 yield, 但内容是完整文件
    assert len(chunks) == 1
    assert len(chunks[0]) == 3 * 1024 * 1024
    assert chunks[0] == b"\x42" * (3 * 1024 * 1024)


@pytest.mark.asyncio
async def test_t3_3_storage_service_default_load_stream_custom_chunk(tmp_path, monkeypatch):
    """基类 load_stream 的 chunk_size 参数被忽略 (默认实现兼容).

    默认实现只调用一次 self.load(), 无论 chunk_size 多少都单次 yield.
    """
    from app.common.storage.storage_service import StorageService
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    big = tmp_path / "default2.bin"
    big.write_bytes(b"\x33" * 1024)

    svc = StorageService()
    chunks: List[bytes] = []
    # 即使传 100 字节 chunk_size, 默认实现仍单次 yield
    async for chunk in svc.load_stream("default2.bin", chunk_size=100):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0] == b"\x33" * 1024


# ============== T3.4: _decode_image_cached 缓存命中 ==============


@pytest.fixture
def fake_png_dir(tmp_path):
    """创建 N 个 fake PNG 文件, 用于 _decode_image_cached 测试.

    文件内容不重要 (PIL 解码对内容是容错的), 我们主要测试 lru_cache 行为.
    """
    img_dir = tmp_path / "imgs"
    img_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(5):
        p = img_dir / f"img_{i}.png"
        # 最小合法 PNG: 1x1 透明像素
        import base64
        minimal_png = base64.b64decode(
            b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        p.write_bytes(minimal_png)
        paths.append(p)
    return paths


def test_t3_4_decode_image_cached_hit(fake_png_dir, monkeypatch):
    """_decode_image_cached 缓存命中: 同 path 二次访问, Image.open 调用次数 = 1.

    验证: functools.lru_cache 生效, 第二次走缓存不调原始函数.
    """
    from app.tasks.ml import classification as cls_mod
    from app.tasks.ml.classification import _decode_image_cached

    # 清空缓存 (避免之前的测试残留)
    _decode_image_cached.cache_clear()

    # 计数: monkeypatch Image.open
    from PIL import Image as PILImage
    call_count = {"n": 0}
    original_open = PILImage.open

    def counting_open(path, *a, **kw):
        call_count["n"] += 1
        return original_open(path, *a, **kw)

    monkeypatch.setattr(cls_mod.Image, "open", counting_open)

    p = str(fake_png_dir[0])
    # 第一次: 触发 open
    img1 = _decode_image_cached(p)
    assert call_count["n"] == 1

    # 第二次: 缓存命中, 不调 open
    img2 = _decode_image_cached(p)
    assert call_count["n"] == 1, f"第二次应走缓存, 但 open 被调了 {call_count['n']} 次"

    # 同一对象 (lru_cache 命中)
    assert img1 is img2

    # cache_info 验证 hits/misses
    info = _decode_image_cached.cache_info()
    assert info.hits == 1
    assert info.misses == 1
    assert info.currsize == 1


def test_t3_4_decode_image_cached_different_paths(fake_png_dir, monkeypatch):
    """不同 path 各自缓存, 但容量限制下会淘汰最旧.

    注: 这里只验证 5 个不同 path 都能各自缓存 (不超 maxsize 时).
    """
    from app.tasks.ml.classification import _decode_image_cached
    from PIL import Image as PILImage

    _decode_image_cached.cache_clear()
    call_count = {"n": 0}
    original_open = PILImage.open

    def counting_open(path, *a, **kw):
        call_count["n"] += 1
        return original_open(path, *a, **kw)

    monkeypatch.setattr(PILImage, "open", counting_open)

    # 5 个不同 path (默认 maxsize=128, 远大于 5)
    for p in fake_png_dir:
        _decode_image_cached(str(p))

    assert call_count["n"] == 5, f"5 个不同 path 应 5 次 open, 实际 {call_count['n']}"

    # 再访问相同 5 个 path, 应全走缓存
    for p in fake_png_dir:
        _decode_image_cached(str(p))
    assert call_count["n"] == 5, f"再次访问应 0 次 open, 实际累计 {call_count['n']}"

    info = _decode_image_cached.cache_info()
    assert info.hits == 5
    assert info.misses == 5
    assert info.currsize == 5


# ============== T3.5: LRU 容量限制 ==============


def test_t3_5_lru_capacity_limit(fake_png_dir, monkeypatch):
    """LRU 容量限制: maxsize=2 时, 第 3 张图淘汰第 1 张.

    验证策略:
    1. 创建一个 maxsize=2 的独立 lru_cache 函数, 避免污染模块缓存
    2. 访问 3 个不同 path
    3. 再访问第 1 个 path → 重新解码 (miss)
    """
    import functools
    from PIL import Image as PILImage

    call_log: List[str] = []

    @functools.lru_cache(maxsize=2)
    def test_decode(path: str):
        call_log.append(path)
        return PILImage.open(path).convert("RGB")

    test_decode.cache_clear()

    # 访问 path 0, 1, 2
    test_decode(str(fake_png_dir[0]))
    test_decode(str(fake_png_dir[1]))
    test_decode(str(fake_png_dir[2]))
    assert len(call_log) == 3

    # path 0, 1 应该被淘汰 (LRU), path 2 最新
    info = test_decode.cache_info()
    assert info.currsize == 2

    # 再次访问 path 0 → 应该 miss (被淘汰)
    test_decode(str(fake_png_dir[0]))
    assert len(call_log) == 4, "path 0 应被淘汰, 再次访问应 miss"

    # path 0 重新进入, path 1 被淘汰
    info = test_decode.cache_info()
    assert info.misses == 4  # 3 个初次 + 1 个 path 0 miss
    assert info.currsize == 2

    # 再次访问 path 1 → miss
    test_decode(str(fake_png_dir[1]))
    assert len(call_log) == 5, "path 1 应被淘汰, 再次访问应 miss"


def test_t3_5_lru_maxsize_configurable(monkeypatch):
    """_decode_image_cached 的 maxsize 从 settings.TRAIN_DECODE_CACHE_SIZE 读取.

    验证:
    1. 修改 settings.TRAIN_DECODE_CACHE_SIZE 后, 新建的 lru_cache 函数 maxsize 改变
    2. 注: 现有模块级 _decode_image_cached 已用 settings 锁定, 改 settings 不会重建
    """
    from app.core.config import settings

    # 现有 maxsize 应 >= 1
    from app.tasks.ml.classification import _decode_image_cached
    current_maxsize = _decode_image_cached.cache_info().maxsize
    assert current_maxsize == max(1, settings.TRAIN_DECODE_CACHE_SIZE)

    # 修改 settings.TRAIN_DECODE_CACHE_SIZE, 验证 maxsize 一致
    monkeypatch.setattr(settings, "TRAIN_DECODE_CACHE_SIZE", 256)
    assert max(1, settings.TRAIN_DECODE_CACHE_SIZE) == 256
    # 现有 _decode_image_cached 已锁定, 但新建一个 lru_cache 函数会读到新值
    import functools

    @functools.lru_cache(maxsize=max(1, settings.TRAIN_DECODE_CACHE_SIZE))
    def new_decode(path: str):
        return None

    new_decode("test")
    assert new_decode.cache_info().maxsize == 256


# ============== T3.6 (Bonus): 与 ImageClassificationDataset 集成 ==============


def test_t3_6_dataset_uses_lru_cache(fake_png_dir, monkeypatch):
    """ImageClassificationDataset.__getitem__ 走 LRU 缓存, 同 path 多次访问只解码 1 次.

    验证集成层: 业务代码使用 LRU 缓存后, DataLoader 多次 __getitem__ 同 idx
    (如测试时反复跑) 不会重复解码.
    """
    from app.tasks.ml.classification import (
        ImageClassificationDataset, _decode_image_cached,
    )
    from PIL import Image as PILImage

    _decode_image_cached.cache_clear()
    call_count = {"n": 0}
    original_open = PILImage.open

    def counting_open(path, *a, **kw):
        call_count["n"] += 1
        return original_open(path, *a, **kw)

    monkeypatch.setattr(PILImage, "open", counting_open)

    samples = [(str(p), i % 3) for i, p in enumerate(fake_png_dir)]
    ds = ImageClassificationDataset(samples)

    # 第一次 __getitem__ 每个 idx 一次
    for i in range(len(samples)):
        _ = ds[i]
    assert call_count["n"] == 5

    # 第二次: 全部走缓存
    for i in range(len(samples)):
        _ = ds[i]
    assert call_count["n"] == 5, (
        f"第二次访问应 0 次 open, 实际累计 {call_count['n']} 次"
    )
