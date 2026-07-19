"""
v2.0.0 迁移脚本幂等性测试
==========================

模拟 v1.0.0 -> v2.0.0 升级: 先建完整 v2.0.0 表, 再用 SQLAlchemy 删掉部分 v2.0.0 列
(模拟旧 v1.0.0 表), 然后跑迁移脚本, 验证缺失列被补齐.

幂等性: 第二次跑应该全部 skip.
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.database import engine, init_db  # noqa: E402
from scripts.migrate_v2_0_0 import MIGRATIONS, get_existing_columns  # noqa: E402


# v1.0.0 旧表不存在的列 (v2.0.0 新加的)
# 注意: SQLite 不支持 DROP 带 INDEX 的列 (task_type 都有 INDEX),
# 所以这里只测 5 个无 INDEX 的 FLOAT 指标列. task_type 列的迁移由
# in-memory init_db 后直接跑 MIGRATIONS 验证 (全 skip 路径).
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
async def test_migrate_v2_0_0_idempotent(file_db, monkeypatch):
    """
    1) init_db 建 v2.0.0 完整表
    2) 删 5 个 v2.0.0 新加的列 (模拟 v1.0.0 旧表)
    3) 跑 MIGRATIONS 逻辑 (与脚本 main 一致), 验证列被补齐
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

        # 3) 跑迁移逻辑
        async with local_engine.begin() as conn:
            for table, column, sqltype, default in MIGRATIONS:
                existing = await get_existing_columns(conn, table)
                if not existing or column in existing:
                    continue
                if default == "NULL":
                    default_clause = " DEFAULT NULL"
                    null_clause = ""
                else:
                    default_clause = f" DEFAULT {default}"
                    null_clause = " NOT NULL"
                sql = (
                    f"ALTER TABLE `{table}` "
                    f"ADD COLUMN `{column}` {sqltype}{default_clause}{null_clause}"
                )
                await conn.execute(text(sql))

        # 验证 5 个被删列已恢复
        async with local_engine.begin() as conn:
            for table, column in [
                ("model_version", "map_50"),
                ("model_version", "map_50_95"),
                ("model_version", "miou"),
                ("model_version", "pixel_accuracy"),
                ("model_version", "dice_score"),
            ]:
                cols = await get_existing_columns(conn, table)
                assert column in cols, f"{table}.{column} 未被迁移补齐"

        # 4) 再跑一次, 全部 skip (幂等性)
        async with local_engine.begin() as conn:
            added_count = 0
            for table, column, sqltype, default in MIGRATIONS:
                existing = await get_existing_columns(conn, table)
                if not existing or column in existing:
                    continue
                added_count += 1
            assert added_count == 0, f"第二次跑应全部 skip, 但还有 {added_count} 列要加"
    finally:
        await local_engine.dispose()
        # engine 是模块级的, 也 dispose 一下
        await engine.dispose()
