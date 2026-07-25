"""
Storage Service: Local Filesystem (开发) / MinIO (生产) 统一接口
================================================================

**v3.0.0 审查修复 P1-1**: 改用 aiofiles 异步 IO, 避免阻塞 event loop

依赖: aiofiles>=23.0 (已在 requirements.txt)
"""
import hashlib
import os
from pathlib import Path
from typing import BinaryIO, Union

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
