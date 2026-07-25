"""
Storage Backends Subpackage
============================

存储后端插件. 提供统一的文件/二进制存储能力.

**Stage 4 新增**. 包含:
- `local` — 本地文件系统 (默认)
- (未来) `minio` — MinIO 对象存储
- (未来) `s3` — Amazon S3
- (未来) `oss` — 阿里云 OSS

**使用方式**:
```python
from app.registry import PluginRegistry
storage = PluginRegistry.get_default("storage")
url = await storage.upload("test.png", b"...", content_type="image/png")
```

**自动发现**:
导入本包会触发 `local` 模块的 PluginRegistry.register() 调用.
"""
# 显式 import, 触发插件自动注册
from plugin.storage_backends.local import LocalStoragePlugin  # noqa: E402, F401

__all__ = ["LocalStoragePlugin"]
