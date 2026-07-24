"""
兼容垫片 (Phase 1.7): 从 app.config 转发到 app.core.config
==========================================================

历史路径, 新代码请直接 `from app.core.config import settings`
本文件将在 Stage 2 完成后删除 (参见 [3层架构重构执行计划.md])

注意: 本垫片只做 re-export, 不执行 settings 初始化副作用 (那由 app.core.config 完成).
"""
# ruff: noqa: F401,F403
from app.core.config import settings  # noqa: F401
