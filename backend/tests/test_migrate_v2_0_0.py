"""
v2.0.0 迁移脚本幂等性测试
==========================

测试目标: app.database.migration.ensure_v2_0_0_schema
(init_db 启动自动调用, 也可手动 python scripts/migrate_v2_0_0.py)

模拟 v1.0.0 -> v2.0.0 升级: 先建完整 v2.0.0 表, 再用 SQLAlchemy 删掉部分
v2.0.0 列 (模拟旧 v1.0.0 表), 然后跑迁移, 验证缺失列被补齐.

幂等性: 第二次跑应该全部 skip.
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.database.migration import (  # noqa: E402
    MIGRATIONS, _get_existing_columns, ensure_v2_0_0_schema,
)


# v1.0.0 旧表不存在的列 (v2.0.0 新加的)
# 注意: SQLite 不支持 DROP 带 INDEX 的列 (task_type 都有 INDEX),
# 所以这里只测 5 个无 INDEX 的 FLOAT 指标列. task_type 列的迁移由
# in-memory init_db 后直接跑 ensure_v2_0_0_schema 验证 (全 skip 路径).
V2_0_0_NEW_COLUMNS = [
    "ALTER TABLE model_version DROP COLUMN map_50",
    "ALTER TABLE model_version DROP COLUMN map_50_95",
    "ALTER TABLE model_version DROP COLUMN miou",
    "ALTER TABLE model_version DROP COLUMN pixel_accuracy",
    "ALTER TABLE model_version DROP COLUMN dice_score",
]


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    """用临时文件 db (sync fixture, 兼容 pytest-asyncio strict 模式)"""
    db_file = tmp_path / "migrate_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    yield db_file
    if db_file.exists():
        db_file.unlink()


@pytest.mark.asyncio
async def test_migrate_v2_0_0_idempotent(file_db):
    """
    1) 建 v2.0.0 完整表
    2) 删 5 个 v2.0.0 新加的列 (模拟 v1.0.0 旧表)
    3) 跑 ensure_v2_0_0_schema, 验证列被补齐
    4) 再跑一次, 全部 skip (幂等)
    """
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415
    local_engine = create_async_engine(f"sqlite+aiosqlite:///{file_db}")

    try:
        # 1) 建 v2.0.0 完整表
        async with local_engine.begin() as conn:
            import app.models  # noqa: F401, PLC0415
            from app.database import Base  # noqa: PLC0415
            await conn.run_sync(Base.metadata.create_all)

        # 2) 删 v2.0.0 新加的列 (模拟 v1.0.0 旧表)
        async with local_engine.begin() as conn:
            for sql in V2_0_0_NEW_COLUMNS:
                await conn.execute(text(sql))

        # 3) 跑迁移 (与 init_db 第二步一致)
        async with local_engine.begin() as conn:
            result = await ensure_v2_0_0_schema(conn)
        assert len(result["added"]) >= 5, f"应补齐至少 5 列, 实际 {result['added']}"
        assert len(result["errors"]) == 0, f"迁移错误: {result['errors']}"

        # 验证 5 个被删列已恢复
        async with local_engine.begin() as conn:
            for table, column in [
                ("model_version", "map_50"),
                ("model_version", "map_50_95"),
                ("model_version", "miou"),
                ("model_version", "pixel_accuracy"),
                ("model_version", "dice_score"),
            ]:
                cols = await _get_existing_columns(conn, table)
                assert column in cols, f"{table}.{column} 未被迁移补齐"

        # 4) 再跑一次, 全部 skip (幂等性)
        async with local_engine.begin() as conn:
            result2 = await ensure_v2_0_0_schema(conn)
        assert len(result2["added"]) == 0, (
            f"第二次跑应全部 skip, 但又加了 {result2['added']}"
        )
    finally:
        await local_engine.dispose()
        from app.database import engine  # noqa: PLC0415
        await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_runs_migration_automatically():
    """
    验证 init_db() 启动时自动跑迁移 (用户零感知修复).
    模拟: monkey-patch engine, 删一列, 调 init_db, 验证列被补齐.
    """
    import tempfile  # noqa: PLC0415
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name

    try:
        from app import database  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
            AsyncSession, async_sessionmaker, create_async_engine,
        )
        test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        test_session = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False,
        )

        orig_engine = database.engine
        orig_session = database.AsyncSessionLocal
        database.engine = test_engine
        database.AsyncSessionLocal = test_session
        try:
            # 1) 建完整 v2.0.0 表
            async with test_engine.begin() as conn:
                import app.models  # noqa: F401, PLC0415
                from app.database import Base  # noqa: PLC0415
                await conn.run_sync(Base.metadata.create_all)

            # 2) 删 model_version 的 5 个 FLOAT 指标列 (SQLite 兼容)
            async with test_engine.begin() as conn:
                for sql in V2_0_0_NEW_COLUMNS:
                    await conn.execute(text(sql))

            # 3) 调 init_db() —— 触发 ensure_v2_0_0_schema
            await database.init_db()

            # 4) 验证: 5 列已恢复
            async with test_engine.begin() as conn:
                cols = await _get_existing_columns(conn, "model_version")
                for col in ["map_50", "map_50_95", "miou", "pixel_accuracy", "dice_score"]:
                    assert col in cols, f"init_db 自动迁移未补齐 model_version.{col}"
        finally:
            database.engine = orig_engine
            database.AsyncSessionLocal = orig_session
            await test_engine.dispose()
    finally:
        if Path(db_path).exists():
            Path(db_path).unlink()


@pytest.mark.asyncio
async def test_migration_module_importable():
    """核心模块可被 import (避免循环 import 把应用启动搞挂)"""
    from app.core import db_migration  # noqa: PLC0415
    assert hasattr(db_migration, "ensure_v2_0_0_schema")
    assert hasattr(db_migration, "MIGRATIONS")
    assert len(db_migration.MIGRATIONS) >= 8
