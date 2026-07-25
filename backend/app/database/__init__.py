"""
Database Package — 数据库基础设施 (横切复用)
==========================================

包含:
- engine (异步引擎)
- base (ORM 基类)
- session (会话管理)
- init_db (初始化)
- redis (Redis 客户端) — v3.0.0 迁入
- migration (数据库迁移) — v3.0.0 迁入
- slow_sql (慢 SQL 监控)

依赖方向: database 仅依赖 core/common, 不依赖 app/*.

兼容垫片: 此包同时作为 app.database 顶层模块的兼容垫片
(原 app.database.py 已被本包替代), 所有旧 import 仍可用:
    from app.database import engine, Base, get_db, init_db
"""
# Re-export 所有公共符号, 保持向后兼容
from app.database.engine import engine, AsyncSessionLocal  # noqa: F401
from app.database.session import get_db, init_db  # noqa: F401
from app.common.base_model import Base  # noqa: F401
# v3.0.0 迁入: Redis 客户端 (从 app.core.redis_client)
from app.database.redis import redis_client, get_redis  # noqa: F401
# v3.0.0 迁入: 数据库迁移 (从 app.core.db_migration)
from app.database.migration import (  # noqa: F401
    MIGRATIONS,
    ensure_v2_0_0_schema,
    _get_existing_columns,
    _column_exists,
    _table_exists,
)

__all__ = [
    "engine",
    "AsyncSessionLocal",
    "Base",
    "get_db",
    "init_db",
    "redis_client",
    "get_redis",
    "MIGRATIONS",
    "ensure_v2_0_0_schema",
]
