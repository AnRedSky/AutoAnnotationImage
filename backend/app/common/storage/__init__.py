"""
app.common.storage — 跨应用文件存储工具
========================================

包含:
- storage_service: 默认 Local filesystem 单例 (开发)
- get_storage_service(): 工厂, 根据 STORAGE_BACKEND 切换 local | minio
- StorageService: 抽象基类 (本地实现)
- MinioStorageService: MinIO 对象存储实现 (生产)

依赖方向: app.common.storage 仅依赖 app.core.config, 不依赖任何业务层.

v3.3.0: 新增 MinIO backend 支持, 通过 STORAGE_BACKEND 环境变量切换.
"""
import logging

from app.common.storage.storage_service import (
    storage_service,
    StorageService,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_storage_service() -> StorageService:
    """工厂: 根据 STORAGE_BACKEND 选择 backend.

    Returns:
      - 'local' (默认): 本地文件系统单例 (storage_service)
      - 'minio': MinioStorageService 实例 (连接到 MINIO_ENDPOINT)
    """
    backend = (settings.STORAGE_BACKEND or "local").lower()
    if backend == "minio":
        from app.common.storage.minio_storage_service import MinioStorageService
        logger.info("Storage backend: MinIO (endpoint=%s, bucket=%s)",
                    settings.MINIO_ENDPOINT, settings.MINIO_BUCKET)
        return MinioStorageService()
    logger.info("Storage backend: local filesystem (path=%s)", settings.UPLOAD_DIR)
    return storage_service


__all__ = [
    "storage_service",
    "StorageService",
    "get_storage_service",
]