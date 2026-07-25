"""
Storage Service: Local Filesystem (开发) / MinIO (生产) 统一接口
"""
import os
import uuid
import hashlib
from pathlib import Path
from typing import BinaryIO
from app.core.config import settings


class StorageService:
    """统一的文件存储接口, 后续可无缝切换到 MinIO/S3"""

    def __init__(self):
        self.base_dir = settings.UPLOAD_DIR

    def _full_path(self, key: str) -> Path:
        return self.base_dir / key

    async def save(self, key: str, content: bytes) -> str:
        """保存文件, 返回相对路径"""
        full = self._full_path(key)
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
        return key

    async def load(self, key: str) -> bytes:
        """读取文件"""
        full = self._full_path(key)
        with open(full, "rb") as f:
            return f.read()

    async def delete(self, key: str) -> None:
        """删除文件"""
        full = self._full_path(key)
        if full.exists():
            full.unlink()

    def exists(self, key: str) -> bool:
        return self._full_path(key).exists()

    @staticmethod
    def compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def generate_key(dataset_id: int, filename: str, content_hash: str) -> str:
        ext = Path(filename).suffix
        return f"datasets/{dataset_id}/{content_hash[:2]}/{content_hash}{ext}"


storage_service = StorageService()
