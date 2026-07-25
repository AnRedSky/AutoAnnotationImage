"""
Core Package — 应用级核心配置
============================

包含:
- config (pydantic-settings 全局配置)
- logging_setup (集中日志配置, Stage 3 新增)
- startup_profiler (启动耗时分析, Stage 5.1 新增)
- cache (Redis-backed 缓存层, Stage 5.2 新增)
- container (依赖注入容器, Stage 5 引入)
- lifespan (FastAPI 生命周期, Stage 2/3 引入)
- security (CORS/CSRF 等)
- cli (命令行入口)

依赖方向: core 不依赖任何业务层.
"""
# 集中日志配置导出 (Stage 3 新增)
from app.core.logging_setup import setup_logging, get_logger  # noqa: E402, F401
# Stage 5.1 启动耗时
from app.core.startup_profiler import startup_profiler, StartupProfiler  # noqa: E402, F401
# Stage 5.2 业务缓存
from app.core.cache import (  # noqa: E402, F401
    cache, cached, invalidate, cache_get, cache_set, Cache,
)

__all__ = [
    "setup_logging", "get_logger",
    "startup_profiler", "StartupProfiler",
    "cache", "cached", "invalidate", "cache_get", "cache_set", "Cache",
]
