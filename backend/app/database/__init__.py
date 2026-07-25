"""
Database Package — 数据库基础设施 (横切复用)
==========================================

包含:
- engine (异步引擎)
- base (ORM 基类)
- session (会话管理)
- init_db (初始化)
- redis (Redis 客户端)
- cache (缓存抽象)
- migrations (Alembic 迁移)

依赖方向: database 仅依赖 core/common, 不依赖 app/*.

兼容垫片: 此包同时作为 app.database 顶层模块的兼容垫片
(原 app.database.py 已被本包替代), 所有旧 import 仍可用:
    from app.database import engine, Base, get_db, init_db
"""
# Re-export 所有公共符号, 保持向后兼容
from app.database.engine import engine, AsyncSessionLocal  # noqa: F401
from app.database.session import get_db, init_db  # noqa: F401
from app.common.base_model import Base  # noqa: F401

__all__ = [
    "engine",
    "AsyncSessionLocal",
    "Base",
    "get_db",
    "init_db",
]
