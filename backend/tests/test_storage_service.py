"""
v2.5.15 P1-9 / D-2 测试: Storage Service
=========================================
覆盖 8 条用例:

 1. test_compute_hash_sha256       SHA256 计算正确
 2. test_generate_key_format       storage_key 格式
 3. test_save_creates_parent_dirs  save 自动建父目录
 4. test_load_roundtrip            save 后 load 内容一致
 5. test_delete_existing           delete 存在文件
 6. test_delete_nonexistent_no_error delete 不存在文件不抛错
 7. test_exists_true_false         exists 判断
 8. test_concurrent_save_no_corruption  并发 save 不互相覆盖
"""
import asyncio
import hashlib
import os
from pathlib import Path

import pytest

from app.services.storage_service import StorageService


# ============== 1. compute_hash ==============

def test_compute_hash_sha256():
    """compute_hash 返回 64 位 hex (SHA256)"""
    content = b"hello world"
    expected = hashlib.sha256(content).hexdigest()
    assert StorageService.compute_hash(content) == expected
    assert len(StorageService.compute_hash(content)) == 64


# ============== 2. generate_key ==============

def test_generate_key_format():
    """storage_key 格式: datasets/{id}/{hash前2}/{hash}{ext}"""
    key = StorageService.generate_key(
        dataset_id=7, filename="cat.png", content_hash="abcdef1234567890",
    )
    assert key.startswith("datasets/7/")
    assert key.endswith(".png")
    assert "/ab/" in key
    assert "abcdef1234567890.png" in key


def test_generate_key_handles_no_ext():
    """无扩展名时也能生成 key"""
    key = StorageService.generate_key(
        dataset_id=1, filename="noext", content_hash="abc123",
    )
    assert key.startswith("datasets/1/")
    assert key.endswith("abc123")  # 无 ext


# ============== 3. save_creates_parent_dirs ==============

@pytest.mark.asyncio
async def test_save_creates_parent_dirs(temp_upload_dir):
    """save 自动创建不存在的父目录"""
    svc = StorageService()
    key = "datasets/1/aa/abc.png"
    await svc.save(key, b"test content")
    full = temp_upload_dir / key
    assert full.exists()
    assert full.parent.is_dir()


# ============== 4. load_roundtrip ==============

@pytest.mark.asyncio
async def test_load_roundtrip(temp_upload_dir):
    """save 后 load 内容一致"""
    svc = StorageService()
    key = "datasets/2/bb/test.txt"
    content = b"roundtrip test content"
    await svc.save(key, content)
    loaded = await svc.load(key)
    assert loaded == content


# ============== 5. delete_existing ==============

@pytest.mark.asyncio
async def test_delete_existing(temp_upload_dir):
    """delete 存在文件 -> 文件被删"""
    svc = StorageService()
    key = "datasets/3/cc/del.txt"
    await svc.save(key, b"to be deleted")
    full = temp_upload_dir / key
    assert full.exists()
    await svc.delete(key)
    assert not full.exists()


# ============== 6. delete_nonexistent_no_error ==============

@pytest.mark.asyncio
async def test_delete_nonexistent_no_error(temp_upload_dir):
    """delete 不存在文件不抛错 (幂等)"""
    svc = StorageService()
    # 不抛异常即可
    await svc.delete("datasets/9/zz/nonexistent.png")
    # 再次删除 (双幂等)
    await svc.delete("datasets/9/zz/nonexistent.png")


# ============== 7. exists ==============

@pytest.mark.asyncio
async def test_exists_true_false(temp_upload_dir):
    """exists 正确判断"""
    svc = StorageService()
    key = "datasets/4/dd/exists.txt"
    assert svc.exists(key) is False
    await svc.save(key, b"hi")
    assert svc.exists(key) is True
    await svc.delete(key)
    assert svc.exists(key) is False


# ============== 8. concurrent save no corruption ==============

@pytest.mark.asyncio
async def test_concurrent_save_no_corruption(temp_upload_dir):
    """10 并发 save 不同 key, 内容不互相覆盖"""
    svc = StorageService()
    n = 10
    keys = [f"datasets/5/ee/concurrent_{i}.txt" for i in range(n)]
    contents = [f"content_{i}_payload".encode() for i in range(n)]

    # 并发 save
    await asyncio.gather(*[svc.save(k, c) for k, c in zip(keys, contents)])

    # 并发 load 验证
    loaded = await asyncio.gather(*[svc.load(k) for k in keys])
    for k, original, got in zip(keys, contents, loaded):
        assert original == got, f"{k}: expected {original!r}, got {got!r}"
