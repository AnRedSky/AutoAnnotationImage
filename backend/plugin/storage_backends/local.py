"""
Local Storage Plugin
====================

本地文件系统存储后端. Stage 4 第一个具体插件实现.

**职责**:
- 把 app/common/storage/storage_service.py 包装为 PluginInterface
- 注册到 PluginRegistry, 作为 storage 分类的默认插件
- 业务代码可通过 PluginRegistry.get_default("storage") 拿到

**依赖方向**:
- plugin/storage_backends/local.py → app.common.storage.storage_service (复用现有实现)
- app.common.storage.storage_service → app.common.interfaces (实现 StorageInterface)
- plugin → 不直接依赖任何 app/* 业务代码

**未来扩展**:
- 高级用户可实现 MinioStorage / S3Storage 替代默认
- 配置切换: 通过 .env STORAGE_BACKEND=local|minio|s3 选择
"""
import logging
from typing import Optional

from app.common.interfaces import PluginInterface
from app.common.storage.storage_service import storage_service
from app.registry import PluginRegistry

logger = logging.getLogger(__name__)


class LocalStoragePlugin(PluginInterface):
    """本地文件系统存储插件 (默认)

    包装现有的 storage_service 单例, 提供 PluginInterface 标准化接入.
    实际存储能力来自 app.common.storage.storage_service.

    Attributes:
        name: "local" (唯一)
        version: "1.0.0"
        category: "storage"
    """

    name = "local"
    version = "1.0.0"
    category = "storage"

    def install(self) -> None:
        """插件安装: 注册到 PluginRegistry (默认)"""
        logger.info(f"LocalStoragePlugin v{self.version}: install (wraps storage_service)")
        # 实际能力委托给 storage_service 单例
        # 这里只做标识, 不重复实现

    def uninstall(self) -> None:
        """插件卸载: 清理资源 (本插件无状态)"""
        logger.info("LocalStoragePlugin: uninstall (no-op, stateless wrapper)")


# ============== 自动注册 ==============
# 导入本模块即触发注册. main.py 在 discover_plugins() 时会触发此 import.
PluginRegistry.register(LocalStoragePlugin(), make_default=True)


__all__ = ["LocalStoragePlugin"]
