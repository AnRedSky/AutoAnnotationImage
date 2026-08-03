"""
v3.4.1 P1 测试: Inference Resolver (local / minio 路径解析)
==========================================================

背景: 当 STORAGE_BACKEND=minio 时, batch_predict 等推理栈走本地文件路径
      会报 FileNotFoundError. resolve_inference_paths 提供适配:
      - local: 直接返回 base_dir/key 路径
      - minio: 临时目录下载, 退出时清理

覆盖:
 1. test_local_backend_returns_base_paths   local 后端零开销直返
 2. test_minio_backend_downloads_to_tmp     minio 后端下载到临时目录
 3. test_minio_backend_cleanup_on_exit      退出上下文时自动清理临时目录
 4. test_minio_backend_concurrent_download  多图并发下载
 5. test_minio_backend_preserves_order      下载顺序与 images 顺序一致
"""
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest

from app.common.storage import resolve_inference_paths


# ============== 辅助: 构造 fake image 对象 ==============

def _make_image(storage_path: str):
    """构造含 .storage_path 属性的简单对象, 模拟 Image ORM 行"""
    return SimpleNamespace(storage_path=storage_path)


# ============== 1. local backend returns base paths ==============

@pytest.mark.asyncio
async def test_local_backend_returns_base_paths(temp_upload_dir):
    """STORAGE_BACKEND=local 时, 直接拼 base_dir + key 返回绝对路径, 不做 IO"""
    with patch("app.common.storage.inference_resolver.settings") as mock_settings:
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.UPLOAD_DIR = temp_upload_dir

        images = [_make_image("datasets/1/aa/img1.png"),
                  _make_image("datasets/1/aa/img2.png")]

        async with resolve_inference_paths(images) as paths:
            assert paths == [
                str(temp_upload_dir / "datasets/1/aa/img1.png"),
                str(temp_upload_dir / "datasets/1/aa/img2.png"),
            ]
            # local 模式: 文件不需要真实存在 (返回路径即可)
            for p in paths:
                assert isinstance(p, str)
                assert p.startswith(str(temp_upload_dir))


# ============== 2. minio backend downloads to tmp ==============

@pytest.mark.asyncio
async def test_minio_backend_downloads_to_tmp(temp_upload_dir):
    """STORAGE_BACKEND=minio 时, 每个 storage_key 走 svc.load 下载到临时目录"""
    # 准备: 1x1 PNG 字节
    png_bytes_1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff"
        b"\x3f\x00\x05\xfe\x02\xfe\xa3wY\xc1\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    png_bytes_2 = png_bytes_1  # 相同内容

    fake_svc = AsyncMock()
    fake_svc.load = AsyncMock(side_effect=[png_bytes_1, png_bytes_2])

    with patch("app.common.storage.inference_resolver.settings") as mock_settings, \
         patch("app.common.storage.inference_resolver.get_storage_service",
               return_value=fake_svc):
        mock_settings.STORAGE_BACKEND = "minio"
        mock_settings.UPLOAD_DIR = temp_upload_dir  # 用于 baseline, 但不读

        images = [_make_image("datasets/5/ee/img1.png"),
                  _make_image("datasets/5/ee/img2.png")]

        async with resolve_inference_paths(images) as paths:
            assert len(paths) == 2
            for p in paths:
                assert Path(p).exists(), f"temp file should exist: {p}"
                # 文件内容是 PNG 字节
                content = Path(p).read_bytes()
                assert content == png_bytes_1
            # 扩展名保留
            assert all(p.endswith(".png") for p in paths)
            # 调用 load 的次数 = 图数
            assert fake_svc.load.call_count == 2
            fake_svc.load.assert_any_call("datasets/5/ee/img1.png")
            fake_svc.load.assert_any_call("datasets/5/ee/img2.png")


# ============== 3. minio backend cleanup on exit ==============

@pytest.mark.asyncio
async def test_minio_backend_cleanup_on_exit(temp_upload_dir):
    """退出 with 块时, 临时目录必须被清理 (不残留文件)"""
    png_bytes = b"fake png content"
    fake_svc = AsyncMock()
    fake_svc.load = AsyncMock(return_value=png_bytes)

    with patch("app.common.storage.inference_resolver.settings") as mock_settings, \
         patch("app.common.storage.inference_resolver.get_storage_service",
               return_value=fake_svc):
        mock_settings.STORAGE_BACKEND = "minio"
        mock_settings.UPLOAD_DIR = temp_upload_dir

        images = [_make_image("datasets/7/gg/cleanup.png")]
        tmp_path_captured = None

        async with resolve_inference_paths(images) as paths:
            tmp_path_captured = paths[0]
            # 临时文件存在
            assert Path(tmp_path_captured).exists()
            # 临时目录的父目录也必须存在
            tmp_dir = Path(tmp_path_captured).parent
            assert tmp_dir.exists()
            assert "img_inference_" in str(tmp_dir)

        # 退出后: 临时目录已被清理
        assert not Path(tmp_path_captured).exists(), \
            f"temp file should be cleaned up: {tmp_path_captured}"
        assert not tmp_dir.exists(), \
            f"temp dir should be cleaned up: {tmp_dir}"


# ============== 4. minio backend concurrent download ==============

@pytest.mark.asyncio
async def test_minio_backend_concurrent_download(temp_upload_dir):
    """多图应并发下载 (而不是串行 await)"""
    import time

    png_bytes = b"x"

    async def slow_load(key: str) -> bytes:
        # 模拟 50ms IO
        import asyncio
        await asyncio.sleep(0.05)
        return png_bytes

    fake_svc = AsyncMock()
    fake_svc.load = AsyncMock(side_effect=slow_load)

    with patch("app.common.storage.inference_resolver.settings") as mock_settings, \
         patch("app.common.storage.inference_resolver.get_storage_service",
               return_value=fake_svc):
        mock_settings.STORAGE_BACKEND = "minio"
        mock_settings.UPLOAD_DIR = temp_upload_dir

        # 5 张图, 串行需 250ms, 并发应 < 100ms
        images = [_make_image(f"datasets/9/ii/img_{i}.png") for i in range(5)]

        t0 = time.monotonic()
        async with resolve_inference_paths(images) as paths:
            elapsed = time.monotonic() - t0
            assert len(paths) == 5
            for p in paths:
                assert Path(p).exists()

        # 留 200ms 余量 (含 CI 抖动); 若串行则需 ~250ms 必挂
        assert elapsed < 0.2, \
            f"downloads should be concurrent, got {elapsed*1000:.0f}ms"


# ============== 5. minio backend preserves order ==============

@pytest.mark.asyncio
async def test_minio_backend_preserves_order(temp_upload_dir):
    """返回的 paths 顺序必须与 images 顺序一致 (用于结果对齐)"""
    payloads = {
        "datasets/1/aa/first.png": b"first",
        "datasets/1/aa/second.png": b"second",
        "datasets/1/aa/third.png": b"third",
    }

    async def load_by_key(key: str) -> bytes:
        return payloads[key]

    fake_svc = AsyncMock()
    fake_svc.load = AsyncMock(side_effect=load_by_key)

    with patch("app.common.storage.inference_resolver.settings") as mock_settings, \
         patch("app.common.storage.inference_resolver.get_storage_service",
               return_value=fake_svc):
        mock_settings.STORAGE_BACKEND = "minio"
        mock_settings.UPLOAD_DIR = temp_upload_dir

        images = [_make_image("datasets/1/aa/first.png"),
                  _make_image("datasets/1/aa/second.png"),
                  _make_image("datasets/1/aa/third.png")]

        async with resolve_inference_paths(images) as paths:
            assert len(paths) == 3
            # 内容顺序与 images 顺序一致
            contents = [Path(p).read_bytes() for p in paths]
            assert contents == [b"first", b"second", b"third"]
