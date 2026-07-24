"""
兼容垫片 (Phase 1.8): 从 app.cli 转发到 app.core.cli
====================================================

历史路径, 新代码请直接 `from app.core.cli import main, build_parser, ...`
本文件将在 Stage 2 完成后删除 (参见 [3层架构重构执行计划.md])
"""
# ruff: noqa: F401,F403
from app.core.cli import (  # noqa: F401
    build_parser,
    print_banner,
    ensure_backend_on_path,
    env_check,
    serve,
    main,
)
