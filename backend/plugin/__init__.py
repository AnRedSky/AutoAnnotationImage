"""
Plugin Package — 顶层插件层 (与 app/ 平级)
========================================

**架构定位**:
- `app/` — 业务应用 (Bounded Context), 自带完整技术栈
- `plugin/` — 可插拔扩展, 独立于业务, 可被多个 app 复用
- 横切目录 (common/core/database/middleware/utils) — 跨切关注点

**v3.0.0 Stage 4 新增**.

**插件分类**:
- `plugin/storage_backends/` — 存储后端 (Local / MinIO / S3)
- `plugin/ml_backends/` — ML 框架后端 (timm / ultralytics / torchvision)
- `plugin/task_queues/` — 任务队列 (Celery / RQ)
- `plugin/notification_channels/` — 通知渠道 (SSE / Email / Webhook)

**插件实现方式**:
1. 继承 `app.common.interfaces.PluginInterface`
2. 实现 `name` / `version` / `category` / `install` / `uninstall`
3. 放入对应分类目录, 例如 `plugin/storage_backends/local.py`
4. 在文件末尾 `PluginRegistry.register(YourPlugin(), make_default=True)`
5. main.py 调用 `PluginRegistry.discover_plugins([...])` 触发自动导入

**与 app/ 的区别**:
- app/ 强业务耦合, 自带 api/service/model
- plugin/ 弱业务耦合, 只提供可替换的能力
- app/ 是必备, plugin/ 可选 (默认实现可独立工作)

**依赖方向**:
- plugin/ → app.common.interfaces (实现接口)
- app/ → plugin/ (使用插件)
- plugin/ → 不依赖任何 app/* 业务代码

**自动发现**:
```python
# main.py 中
from app.registry import PluginRegistry
PluginRegistry.discover_plugins(["storage_backends", "ml_backends", "task_queues", "notification_channels"])
```
"""
# 重导出 PluginRegistry 方便 plugin 内部使用
from app.registry import PluginRegistry

__all__ = ["PluginRegistry"]

