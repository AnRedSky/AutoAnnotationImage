"""
model API Package (v3.0.0 Phase R 拆分)
========================================

**职责**: 模型版本管理 (查询/激活/删除)

**目录结构** (拆分自原 model.py, 503 行 → 4 子模块):
- query.py        列表查询 + 详情查询 + 激活模型查询 (3 路由, 含 path="" 兼容旧 URL)
- activation.py   单个/批量激活 + 单个取消激活 (3 路由 + BatchActivateRequest schema)
- deletion.py     单个/批量删除 + 权重文件清理 + training_job 解绑 (2 路由 + BatchDeleteRequest schema + _delete_one_model 工具)

**v3.0.0 Phase R 拆分**:
- 从原 model.py (503 行) 拆出 3 个子模块, 每个模块只关注单一职责
- query_router 作为顶层 router (含 path="" 兼容路由), 其他子 router 通过 include_router 拼装
- 旧 model.py 文件已删除 (避免与本包同名冲突)
- 删除了未使用的 `_lock_dataset_models` 死代码工具函数
- Pydantic Schemas 分散到对应模块 (BatchActivateRequest → activation, BatchDeleteRequest → deletion), 与路由同文件就近定义

**对外接口 (完全向后兼容)**: 9 个路由保持不变
- GET    /api/models                     (query)
- GET    /api/models/                    (query, 兼容尾斜杠)
- GET    /api/models/active              (query)
- GET    /api/models/{model_id}/detail   (query)
- POST   /api/models/{model_id}/activate     (activation)
- POST   /api/models/{model_id}/deactivate   (activation)
- POST   /api/models/batch-activate          (activation)
- DELETE /api/models/{model_id}              (deletion)
- POST   /api/models/batch-delete            (deletion)
"""
from fastapi import APIRouter

from app.tasks.api.model.query import router as query_router
from app.tasks.api.model.activation import router as activation_router
from app.tasks.api.model.deletion import router as deletion_router

# 拼装顶层 router:
# 关键: query_router 包含 path="" 和 path="/" 的 list_models 路由 (兼容 /api/models 和 /api/models/)
# FastAPI 不允许父 router.prefix="" + 子 path="" 的组合, 因此直接把 query_router 作为顶层 router
# 然后把 activation/deletion 拼装到 query_router 上 (它们的 path 都是 "/{xxx}" 形式, 不冲突)
router = query_router
router.include_router(activation_router)
router.include_router(deletion_router)

# 命名导出 (供 tasks/__init__.py import)
model_router = router  # 兼容旧别名


__all__ = [
    "router",
    "model_router",
    "query_router",
    "activation_router",
    "deletion_router",
]
