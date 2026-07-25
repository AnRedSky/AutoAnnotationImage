"""
Core Package — 应用级核心配置
============================

包含:
- config (pydantic-settings 全局配置)
- logging_setup (集中日志配置, Stage 3 新增)
- container (依赖注入容器, Stage 5 引入)
- lifespan (FastAPI 生命周期, Stage 2/3 引入)
- security (CORS/CSRF 等)
- cli (命令行入口)

依赖方向: core 不依赖任何业务层.
"""
# 集中日志配置导出 (Stage 3 新增)
from app.core.logging_setup import setup_logging, get_logger  # noqa: E402, F401

__all__ = ["setup_logging", "get_logger"]
