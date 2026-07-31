"""
MinIO Storage Service (v3.3.0)
=================================

实现 StorageService 接口, 用 MinIO 作为对象存储后端.

设计:
  - 继承 StorageService 复用 compute_hash / generate_key (backend-agnostic)
  - 重写 save / save_stream / load / delete / exists 用 minio SDK
  - 用 asyncio.to_thread 包装同步 SDK 调用 (minio 7.x 是同步的)
  - 启动时 _ensure_bucket() 幂等创建 bucket
  - _base_dir 留 None, _full_path 抛 NotImplementedError (MinIO 没有路径概念)

配置:
  STORAGE_BACKEND=minio
  MINIO_ENDPOINT=127.0.0.1:9000
  MINIO_ACCESS_KEY=minioadmin
  MINIO_SECRET_KEY=***
  MINIO_BUCKET=image-annotation
  MINIO_SECURE=false
"""
import asyncio
import io
import logging
from pathlib import Path
from typing import BinaryIO, Union

from minio import Minio
from minio.error import S3Error

from app.common.storage.storage_service import StorageService
from app.core.config import settings

logger = logging.getLogger(__name__)


class MinioStorageService(StorageService):
    """MinIO 对象存储后端 (生产环境推荐, 支持多 worker / 多机)."""

    def __init__(self):
        # 注意: 不调 super().__init__() — base_dir 对 MinIO 无意义
        self._client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self._bucket = settings.MINIO_BUCKET
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """启动时幂等创建 bucket (minio SDK 同步)."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info(f"MinIO bucket created: {self._bucket}")
            else:
                logger.info(f"MinIO bucket exists: {self._bucket}")
        except S3Error as e:
            logger.error(f"MinIO bucket check/create failed: {e!r}")
            raise

    def _full_path(self, key: str) -> Path:
        # MinIO 没有文件路径概念; 防路径穿越用 key 校验替代
        if not key or ".." in key.split("/") or key.startswith("/"):
            raise ValueError(f"Invalid storage key (path traversal): {key!r}")
        raise NotImplementedError("MinIO storage has no _full_path; use key directly")

    async def save(self, key: str, content: bytes) -> str:
        """上传 bytes 到 MinIO. 返回 key."""
        data = io.BytesIO(content)
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            key,
            data,
            len(content),
        )
        return key

    async def save_stream(
        self,
        key: str,
        source: Union[BinaryIO, "aiofiles.tempfile.NamedTemporaryFile"],
    ) -> str:
        """流式上传: 先读完到 buffer, 再 put_object.
        (minio SDK 的 put_object 需要可 seekable + length, 不支持纯 async 迭代)
        """
        chunks: list[bytes] = []
        if hasattr(source, "read"):
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        else:
            async for chunk in source:  # type: ignore[union-attr]
                chunks.append(chunk)
        content = b"".join(chunks)
        return await self.save(key, content)

    async def load(self, key: str) -> bytes:
        """下载文件全部内容."""
        def _fetch() -> bytes:
            resp = self._client.get_object(self._bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        try:
            return await asyncio.to_thread(_fetch)
        except S3Error as e:
            if e.code in ("NoSuchKey", "NoSuchObject"):
                raise FileNotFoundError(f"MinIO object not found: {key}") from e
            raise

    async def delete(self, key: str) -> None:
        """幂等删除."""
        try:
            await asyncio.to_thread(self._client.remove_object, self._bucket, key)
        except S3Error as e:
            if e.code in ("NoSuchKey", "NoSuchObject"):
                return  # 幂等: 不存在当成功
            raise

    def exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except S3Error as e:
            if e.code in ("NoSuchKey", "NoSuchObject"):
                return False
            raise

    def get_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        """生成临时直链 URL (前端可绕过 API 直接下载, 减轻 API 压力)."""
        from datetime import timedelta
        return self._client.presigned_get_object(
            self._bucket, key, expires=timedelta(seconds=expires_seconds)
        )