"""
app.common.storage — 跨应用文件存储工具

包含:
- storage_service: 文件存储 / 路径解析 (原 app.common.storage.storage_service)

依赖方向: app.common.storage 仅依赖 app.core.config, 不依赖任何业务层.
"""
from app.common.storage.storage_service import (
    storage_service,
    StorageService,
)


__all__ = [
    "storage_service",
    "StorageService",
]
