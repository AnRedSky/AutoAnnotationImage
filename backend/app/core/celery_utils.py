"""
兼容垫片 (Phase 1.6): 从 app.core.celery_utils 转发到 app.utils.async_helpers
============================================================================

历史路径, 新代码请直接 `from app.utils.async_helpers import ...`
本文件将在 Stage 2 完成后删除 (参见 [3层架构重构执行计划.md])
"""
# ruff: noqa: F401,F403
from app.utils.async_helpers import (  # noqa: F401
    check_celery_available,
    run_async_in_worker,
)
