"""
Celery Task Queue Plugin
========================

异步任务队列后端. Stage 4 第二个具体插件实现.

**职责**:
- 包装 `app.tasks.workers.celery_app.celery_app` 为 PluginInterface
- 提供统一的 enqueue / get_state / revoke 入口
- 业务代码通过 `PluginRegistry.get_default("task_queue")` 获取, 替换实现零侵入

**依赖方向**:
- plugin/task_queues/celery.py → app.tasks.workers.celery_app (复用现有 Celery)
- plugin → 不直接依赖任何 app/* 业务代码 (除 Celery app 实例)

**未来扩展**:
- RQBackend: 用 Redis Queue 替代 Celery
- DramatiqBackend: 用 Dramatiq 替代
- 切换方式: 在 main.py 注册另一种 TaskQueuePlugin 为默认, 业务代码无感
"""
import logging
from typing import Any, Optional

from app.common.interfaces import PluginInterface
from app.registry import PluginRegistry

logger = logging.getLogger(__name__)


class CeleryTaskQueuePlugin(PluginInterface):
    """Celery 任务队列插件 (默认)

    包装现有的 celery_app 单例, 提供 PluginInterface 标准化接入.
    实际任务能力来自 app.tasks.workers.celery_app.

    Attributes:
        name: "celery" (唯一)
        version: "1.0.0"
        category: "task_queue"
    """

    name = "celery"
    version = "1.0.0"
    category = "task_queue"

    def install(self) -> None:
        """插件安装: 确认 Celery app 已就绪"""
        from app.tasks.workers.celery_app import celery_app  # noqa: PLC0415
        self._app = celery_app
        logger.info(
            f"CeleryTaskQueuePlugin v{self.version}: install (wraps celery_app={celery_app.main})"
        )

    def uninstall(self) -> None:
        """插件卸载: 清理引用 (Celery 由 worker 进程管理)"""
        logger.info("CeleryTaskQueuePlugin: uninstall (worker进程负责 Celery 生命周期)")
        self._app = None  # type: ignore[assignment]

    # ============== TaskQueueInterface 适配方法 ==============
    # 业务代码不直接调用, 但 PluginInterface 包含 install/uninstall 即可;
    # 业务层通过 PluginRegistry.get("task_queue", "celery").enqueue(...) 调用
    # 这里提供便捷方法包装 Celery API.

    def enqueue(self, task_name: str, *args: Any, **kwargs: Any) -> str:
        """提交任务, 返回 task_id

        Args:
            task_name: Celery 任务全名 (e.g. "app.tasks.workers.classification.run_training")
            *args: 任务位置参数
            **kwargs: 任务关键字参数
        """
        app = self._ensure_app()
        task = app.send_task(task_name, args=args, kwargs=kwargs)
        logger.debug(f"CeleryTaskQueuePlugin: enqueued {task_name} -> {task.id}")
        return task.id

    def get_state(self, task_id: str) -> str:
        """获取任务状态 (PENDING / PROGRESS / SUCCESS / FAILURE / REVOKED)"""
        app = self._ensure_app()
        result = app.AsyncResult(task_id)
        return result.state

    def get_result(self, task_id: str, timeout: Optional[float] = None) -> Any:
        """获取任务结果 (阻塞等待)"""
        app = self._ensure_app()
        result = app.AsyncResult(task_id)
        return result.get(timeout=timeout, propagate=True)

    def revoke(self, task_id: str, terminate: bool = False) -> None:
        """取消任务

        Args:
            task_id: 任务 ID
            terminate: 是否强制终止 (True=TERM信号, 仅 prefork 池有效)
        """
        app = self._ensure_app()
        app.control.revoke(task_id, terminate=terminate)
        logger.info(f"CeleryTaskQueuePlugin: revoked {task_id} (terminate={terminate})")

    # ============== 内部辅助 ==============
    def _ensure_app(self):
        """确保 Celery app 已加载 (install 后置入)"""
        if not hasattr(self, "_app") or self._app is None:
            # 兜底: 延迟加载, 允许插件未 install 时调用
            from app.tasks.workers.celery_app import celery_app
            self._app = celery_app
        return self._app


# ============== 自动注册 ==============
# 导入本模块即触发注册. main.py 在 discover_plugins() 时会触发此 import.
# 注: install() 在 main.py 的 lifespan startup 中通过遍历 PluginRegistry 显式触发.
PluginRegistry.register(CeleryTaskQueuePlugin(), make_default=True)


__all__ = ["CeleryTaskQueuePlugin"]
