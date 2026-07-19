"""
v2.0.0 迁移脚本: 补齐 v1.0.0 已有表的缺失列
=============================================

背景
----
v2.0.0 在以下 3 张已有表上加了新列, 但 init_db() 用的是 Base.metadata.create_all(),
该方法**只创建不存在的表, 不会修改已有表的列**. 因此从 v1.0.0 升级到 v2.0.0 的用户
(尤其生产 MySQL) 会遇到 `Unknown column 'xxx.yyy' in 'field list'` 错误.

本脚本做**幂等列添加**:
- 用 inspect 检查每张表的关键列是否存在
- 缺则 ALTER TABLE ADD COLUMN, 加 DEFAULT 'classification' (向后兼容)
- MySQL + SQLite 都支持 (类型都用 VARCHAR(32) / FLOAT, 不依赖方言)

用法
----
    cd backend
    python scripts/migrate_v2_0_0.py

幂等性
----
重复执行不会报错: 第二次起所有列已存在, 直接跳过.

不需要 alembic: 走原生 SQL, 比 alembic upgrade 简单, 适合 v1.0.0 → v2.0.0 这种
单次结构补齐场景. 后续更复杂的迁移 (v2.1.0+) 建议改用 alembic.
"""
import asyncio
import sys
from pathlib import Path

# 让脚本能 import app.* (项目根目录加入 sys.path)
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from app.config import settings  # noqa: E402


# ============== 待补列定义 (table, column, SQL TYPE, DEFAULT) ==============
# - type: SQLAlchemy 通用类型, MySQL/SQLite 都能解析
#   VARCHAR(32) 同时兼容 MySQL VARCHAR 和 SQLite TEXT
#   FLOAT 同时兼容 MySQL FLOAT 和 SQLite REAL
# - default: 'NULL' 表示允许 NULL (FLOAT 指标列); 其他为字面 DEFAULT 表达式
# 索引: 任何叫 task_type 的列自动加索引 (业务高频查询字段)
MIGRATIONS = [
    # training_jobs
    ("training_jobs", "task_type", "VARCHAR(32)", "'classification'"),
    # model_version
    ("model_version", "task_type",       "VARCHAR(32)", "'classification'"),
    ("model_version", "map_50",          "FLOAT",       "NULL"),
    ("model_version", "map_50_95",       "FLOAT",       "NULL"),
    ("model_version", "miou",            "FLOAT",       "NULL"),
    ("model_version", "pixel_accuracy",  "FLOAT",       "NULL"),
    ("model_version", "dice_score",      "FLOAT",       "NULL"),
    # image
    ("image", "task_type", "VARCHAR(32)", "'classification'"),
]


def get_url() -> str:
    """与 app.database 一致, 但支持 -d / -url 覆盖 (调试用)"""
    for i, arg in enumerate(sys.argv):
        if arg in ("-d", "--database-url") and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return settings.EFFECTIVE_DATABASE_URL


async def get_existing_columns(conn, table: str) -> set:
    """用 inspect 拿表的实际列名集合, 兼容 MySQL/SQLite
    注意: SQLAlchemy 2.0 的 inspect 不支持 AsyncConnection,
    必须放在 run_sync 里跑, 由 inspect 拿到 sync Connection."""
    from sqlalchemy import inspect

    def _probe(sync_conn):
        insp = inspect(sync_conn)
        if not insp.has_table(table):
            return set()
        return {c["name"] for c in insp.get_columns(table)}

    return await conn.run_sync(_probe)


async def main() -> int:
    url = get_url()
    print(f"[migrate_v2_0_0] DATABASE_URL = {url}")
    engine = create_async_engine(url)
    total_added = 0
    total_skipped = 0
    try:
        async with engine.begin() as conn:
            for table, column, sqltype, default, nullable_default in MIGRATIONS:
                existing = await get_existing_columns(conn, table)
                if not existing:
                    print(f"  [skip] {table}.{column}: 表不存在 (create_all 会自动建)")
                    total_skipped += 1
                    continue
                if column in existing:
                    print(f"  [skip] {table}.{column}: 已存在")
                    total_skipped += 1
                    continue
                # 缺则 ADD COLUMN
                # NOT NULL 时必须给 DEFAULT, 兼容已有行
                null_clause = "" if nullable_default else " NOT NULL"
                if default == "NULL":
                    default_clause = " DEFAULT NULL"
                else:
                    default_clause = f" DEFAULT {default}"
                # 单表 ADD COLUMN 多列的语法: MySQL/SQLite 都支持 ALTER TABLE ADD COLUMN
                sql = (
                    f"ALTER TABLE `{table}` "
                    f"ADD COLUMN `{column}` {sqltype}{default_clause}{null_clause}"
                )
                print(f"  [add]  {table}.{column}: {sqltype}{default_clause}{null_clause}")
                await conn.execute(text(sql))
                # 索引 (task_type 是高频查询字段, 单独建索引)
                if column == "task_type":
                    idx_name = f"ix_{table}_{column}"
                    idx_sql = f"CREATE INDEX `{idx_name}` ON `{table}` (`{column}`)"
                    try:
                        await conn.execute(text(idx_sql))
                        print(f"         + index {idx_name}")
                    except Exception as e:  # noqa: BLE001
                        # MySQL 8 之前可能因列定义里已 INDEX 重复建, 忽略
                        print(f"         (索引 {idx_name} 已存在或建失败, 跳过: {e})")
                total_added += 1
        print(f"\n[migrate_v2_0_0] 完成: 新增 {total_added} 列, 跳过 {total_skipped}")
        return 0
    except Exception as e:
        print(f"\n[migrate_v2_0_0] 失败: {e!r}")
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
