"""
抽象接口层 (Common Layer)
=========================

定义应用和插件必须实现的接口, 支持多应用 + 插件化架构 (Stage 2-4).

**核心接口**:
- `AppInterface`: 业务应用 (Bounded Context) 接口 — 每个 app/{name}/ 目录实现一个
- `PluginInterface`: 插件接口 — plugin/{category}/{name}.py 实现一个
- `StorageInterface`: 存储后端接口 (Stage 4 插件化)
- `MLBackendInterface`: ML 框架后端接口 (Stage 4 插件化)
- `TaskQueueInterface`: 任务队列接口 (Stage 4 插件化)

**依赖方向**:
- common/interfaces.py 不依赖任何业务代码
- app/*/ 依赖 common/interfaces.py (实现 AppInterface)
- plugin/*/ 依赖 common/interfaces.py (实现 PluginInterface)

v3.0.0 Stage 2 新增
v3.0.0 Stage 2.7: 新增 RouteEntry NamedTuple + AppInterface.get_routes() 默认实现
"""
from abc import ABC, abstractmethod
from typing import Any, Callable, List, NamedTuple, Optional

from fastapi import APIRouter


# ============== RouteEntry (Stage 2.7 新增) ==============

class RouteEntry(NamedTuple):
    """应用路由条目: (router, prefix, tags)

    用于 AppInterface.get_routes() 返回值, main.py 用它批量挂载.
    一个应用可以返回多个 RouteEntry, 对应多个前缀的路由组.

    Example:
        >>> entries = [
        ...     RouteEntry(user_router, "/api/users", ["用户管理"]),
        ...     RouteEntry(stats_router, "/api/stats", ["统计分析"]),
        ... ]
    """
    router: APIRouter
    prefix: str = ""
    tags: List[str] = []


# ============== AppInterface ==============

class AppInterface(ABC):
    """所有业务应用必须实现的接口 (Bounded Context 入口)

    应用是自治的业务单元, 自带完整技术栈 (api/service/model/schema).
    应用之间不直接 import, 通过 EventBus 通信 (见 common/events.py).

    必填属性:
    - name: 应用名 (e.g. "admin", "auth", "tasks", "annotation")
    - version: 应用版本 (语义化版本)
    - router: FastAPI APIRouter, 包含本应用所有路由 (默认聚合根)

    可选方法 (Stage 2.7 增强):
    - get_routes: 返回 (router, prefix, tags) 三元组列表, 用于多 prefix 场景
      默认实现: 返回 [(self.router, "", [])]
      子类可 override 返回多个条目.
    - register_events: 返回本应用订阅的领域事件列表
    - startup: 应用启动时执行 (e.g. 预热缓存, 注册定时任务)
    - shutdown: 应用关闭时执行 (e.g. 释放资源)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """应用名 (唯一, 用于注册和路由前缀)"""

    @property
    @abstractmethod
    def version(self) -> str:
        """应用版本 (语义化, e.g. '1.0.0')"""

    @property
    @abstractmethod
    def router(self) -> APIRouter:
        """FastAPI 路由 (本应用全部端点的聚合根)"""

    def get_routes(self) -> List[RouteEntry]:
        """返回本应用的所有路由条目 (router, prefix, tags)

        默认实现: 单一 router, 无 prefix, 无 tags.
        子类可 override 返回多个条目, 例如 admin:
            [(user_router, "/api/users", ["用户管理"]),
             (stats_router, "/api/stats", ["统计分析"]),
             (system_router, "/api", ["系统"])]
        """
        return [RouteEntry(router=self.router, prefix="", tags=[])]

    def register_events(self) -> List[str]:
        """本应用订阅的领域事件名列表 (用于 EventBus.subscribe)"""
        return []

    async def startup(self) -> None:
        """应用启动钩子 (默认空)"""

    async def shutdown(self) -> None:
        """应用关闭钩子 (默认空)"""


# ============== PluginInterface ==============

class PluginInterface(ABC):
    """所有插件必须实现的接口

    插件是独立可插拔的扩展, 不依赖具体业务, 可被多个 app 复用.
    启动时根据配置注册一个或多个实现, 通过 PluginRegistry 索引.

    必填属性:
    - name: 插件名 (唯一, e.g. "minio_storage", "timm_classification")
    - version: 插件版本
    - category: 插件分类 ("storage" / "ml_backend" / "task_queue" / "auth_provider")

    必填方法:
    - install: 插件安装钩子 (注册路由/事件/服务到目标 app)
    - uninstall: 插件卸载钩子
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名 (唯一)"""

    @property
    @abstractmethod
    def version(self) -> str:
        """插件版本"""

    @property
    @abstractmethod
    def category(self) -> str:
        """插件分类 (storage / ml_backend / task_queue / auth_provider)"""

    @abstractmethod
    def install(self) -> None:
        """插件安装: 注册到全局注册中心, 提供能力"""

    @abstractmethod
    def uninstall(self) -> None:
        """插件卸载: 清理资源"""


# ============== 存储接口 (Stage 4 抽象) ==============

class StorageInterface(ABC):
    """存储后端抽象接口

    实现: LocalStorage (plugin/storage_backends/local.py) / MinioStorage / S3Storage / AliyunOSS
    """

    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        """上传数据, 返回访问 URL"""

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """下载数据"""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """删除数据, 返回是否成功"""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """检查数据是否存在"""

    @abstractmethod
    def get_url(self, key: str, expires: int = 3600) -> str:
        """生成访问 URL (带过期时间)"""


# ============== ML Backend 接口 (Stage 4 抽象) ==============

class MLBackendInterface(ABC):
    """ML 框架后端抽象接口

    实现: TimmBackend (分类) / UltralyticsBackend (检测) / TorchvisionBackend (分割)
    """

    @property
    @abstractmethod
    def task_type(self) -> str:
        """任务类型: classification / detection / segmentation"""

    @abstractmethod
    def build_model(self, base_model: str, num_classes: int, **kwargs) -> Any:
        """构建模型实例"""

    @abstractmethod
    def train(self, model: Any, train_data: Any, val_data: Any, **kwargs) -> Any:
        """训练模型, 返回训练结果 (含历史曲线)"""

    @abstractmethod
    def predict(self, model: Any, image: Any, **kwargs) -> Any:
        """单图推理, 返回预测结果"""


# ============== Task Queue 接口 (Stage 4 抽象) ==============

class TaskQueueInterface(ABC):
    """任务队列抽象接口

    实现: CeleryBackend (默认) / RQBackend / DramatiqBackend
    """

    @abstractmethod
    def enqueue(self, task_name: str, *args: Any, **kwargs: Any) -> str:
        """提交任务, 返回 task_id"""

    @abstractmethod
    def get_result(self, task_id: str, timeout: Optional[float] = None) -> Any:
        """获取任务结果 (阻塞等待)"""

    @abstractmethod
    def get_state(self, task_id: str) -> str:
        """获取任务状态 (PENDING / PROGRESS / SUCCESS / FAILURE)"""

    @abstractmethod
    def revoke(self, task_id: str, terminate: bool = False) -> None:
        """取消任务"""


# ============== Type hints for event handlers ==============

EventHandler = Callable[[dict], Any]
"""事件处理函数签名: (event_payload: dict) -> Any"""


__all__ = [
    "RouteEntry",
    "AppInterface",
    "PluginInterface",
    "StorageInterface",
    "MLBackendInterface",
    "TaskQueueInterface",
    "EventHandler",
]
