"""
Storage Service: Local Filesystem (开发) / MinIO (生产) 统一接口
================================================================

**v3.0.0 审查修复 P1-1**: 改用 aiofiles 异步 IO, 避免阻塞 event loop

依赖: aiofiles>=23.0 (已在 requirements.txt)
"""
import hashlib
import os
from pathlib import Path
from typing import AsyncIterator, BinaryIO, Union

import aiofiles
import aiofiles.os

from app.core.config import settings


class StorageService:
    """统一的文件存储接口, 后续可无缝切换到 MinIO/S3"""

    def __init__(self):
        self.base_dir = settings.UPLOAD_DIR

    def _full_path(self, key: str) -> Path:
        full = self.base_dir / key
        # 路径穿越防御 (v3.0.0 审查修复): 确保在 base_dir 内
        try:
            full.resolve().relative_to(self.base_dir.resolve())
        except ValueError:
            raise ValueError(f"Invalid storage key (path traversal): {key!r}")
        return full

    async def save(self, key: str, content: bytes) -> str:
        """保存文件, 返回相对路径 (v3.0.0 改用 aiofiles)"""
        full = self._full_path(key)
        full.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(full, "wb") as f:
            await f.write(content)
        return key

    async def save_stream(self, key: str, source: Union[BinaryIO, "aiofiles.tempfile.NamedTemporaryFile"]) -> str:
        """流式保存 (大文件推荐, 避免一次性 read() 全量在内存)"""
        full = self._full_path(key)
        full.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(full, "wb") as f:
            if hasattr(source, "read"):
                while True:
                    chunk = source.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    await f.write(chunk)
            else:
                # 已是异步文件
                async for chunk in source:  # type: ignore[union-attr]
                    await f.write(chunk)
        return key

    async def load(self, key: str) -> bytes:
        """读取文件 (v3.0.0 改用 aiofiles)"""
        full = self._full_path(key)
        async with aiofiles.open(full, "rb") as f:
            return await f.read()

    async def load_stream(self, key: str, chunk_size: int = 1 << 20) -> AsyncIterator[bytes]:
        """流式下载文件, 默认 1MB chunks.

        v3.6.0 P3: 默认实现是按 load() 一次性返回后, 假装分块.
        大多数 backend (local / MinIO) 应重写本方法实现真流式, 避免大对象
        (如 50MB 训练图片) 一次性驻留内存. 调用方通常用法:

            async for chunk in storage_service.load_stream(key):
                await f.write(chunk)

        Args:
            key: 存储 key
            chunk_size: 每个 chunk 字节数, 默认 1MB. backend 可在实现中尊重或忽略.
        """
        # 默认实现: 走 load() 一次性返回, 单 chunk 形态. 子类重写可降低内存峰值.
        yield await self.load(key)

    async def delete(self, key: str) -> None:
        """删除文件 (v3.0.0 改用 aiofiles.os)"""
        full = self._full_path(key)
        try:
            await aiofiles.os.unlink(full)
        except FileNotFoundError:
            pass  # 幂等删除
        except AttributeError:
            # aiofiles.os 老版本可能没有 unlink, 降级
            if full.exists():
                os.unlink(full)

    def exists(self, key: str) -> bool:
        return self._full_path(key).exists()

    @staticmethod
    def compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def generate_key(dataset_id: int, filename: str, content_hash: str) -> str:
        # v3.0.0 审查修复: 使用 safe_filename 清理原始文件名
        from app.utils.file_utils import safe_filename
        ext = Path(safe_filename(filename)).suffix or ".bin"
        return f"datasets/{dataset_id}/{content_hash[:2]}/{content_hash}{ext}"


storage_service = StorageService()


def _real_storage_service():
    """根据 STORAGE_BACKEND 决定实际 backend.

    v3.3.0: 让所有调用方 (upload.py / files.py 等) 继续从 storage_service 单例
    获取, 但底层按环境变量切换 local / minio.
    注意: 调用方必须从 storage_service 调方法, 不能缓存实例 (backend 可热切换).
    """
    backend = (settings.STORAGE_BACKEND or "local").lower()
    if backend == "minio":
        from app.common.storage.minio_storage_service import MinioStorageService
        global _minio_singleton
        if _minio_singleton is None:
            _minio_singleton = MinioStorageService()
        return _minio_singleton
    return _local_singleton


# Local 单例 (模块级 cache, 避免每次 _real_storage_service() 都新建)
_local_singleton: StorageService = storage_service
_minio_singleton: "MinioStorageService | None" = None


class _LazyStorageProxy:
    """透明代理: 每次访问属性都路由到当前 backend 实例.

    解决 Python 'from x import y' 的对象绑定陷阱 — 调用方拿到的是 proxy,
    方法调用时才查 backend, 这样 backend 切换对调用方完全透明.
    """

    __slots__ = ()

    def __getattr__(self, name: str):
        return getattr(_real_storage_service(), name)

    def __repr__(self) -> str:
        return f"<LazyStorageProxy -> {type(_real_storage_service()).__name__}>"


# 替换 storage_service 为代理
storage_service = _LazyStorageProxy()
