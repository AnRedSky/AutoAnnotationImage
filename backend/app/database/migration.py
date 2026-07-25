"""
v2.0.0 数据库迁移 (database 子包, init_db 启动自动调用)
=====================================================

v3.0.0 迁移: 从 app.core.db_migration 迁入 app.database.migration (与 engine/session 同级, 属于 DB 基础设施)

背景:
  v2.0.0 在 3 张已有表 (training_jobs / model_version / image) 加了列,
  但 Base.metadata.create_all() 只建新表不补列. v1.0.0 升级用户会报
  `Unknown column 'training_jobs.task_type' in 'field list'`.

设计:
  - 与 ORM 模型解耦: 用原生 SQL + information_schema 检查列/表是否存在
  - 兼容 MySQL + SQLite: 两种方言都能跑 (SQLite 无 information_schema.COLUMNS,
    退化到 PRAGMA table_info, 由 dialect 自适应)
  - 幂等: 重复跑全部 skip, 不会破坏数据
  - 启动自动: app.database.init_db() 在 create_all 后自动调用

调用:
  from app.database.migration import ensure_v2_0_0_schema
  async with engine.begin() as conn:
      await ensure_v2_0_0_schema(conn)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# ============== 待补列定义 (table, column, SQL TYPE, DEFAULT) ==============
# - type: 通用类型, MySQL/SQLite 都能解析
#   VARCHAR(32) 同时兼容 MySQL VARCHAR 和 SQLite TEXT
#   FLOAT 同时兼容 MySQL FLOAT 和 SQLite REAL
# - default: 'NULL' 表示允许 NULL (FLOAT 指标列); 其他为字面 DEFAULT 表达式
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


async def _get_existing_columns(conn: AsyncConnection, table: str) -> set:
    """用 inspect 拿表的实际列名集合, 兼容 MySQL/SQLite
    SQLAlchemy 2.0 的 inspect 不支持 AsyncConnection, 必须放在 run_sync 里.
    """
    from sqlalchemy import inspect  # noqa: PLC0415

    def _probe(sync_conn: Any) -> set:
        insp = inspect(sync_conn)
        if not insp.has_table(table):
            return set()
        return {c["name"] for c in insp.get_columns(table)}

    return await conn.run_sync(_probe)


async def _column_exists(conn: AsyncConnection, table: str, column: str) -> bool:
    """轻量级单列检查: 优先 information_schema, 退化到 inspect"""
    from sqlalchemy import inspect  # noqa: PLC0415
    try:
        rows = await conn.execute(text(
            "SELECT 1 FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "  AND TABLE_NAME = :t "
            "  AND COLUMN_NAME = :c LIMIT 1"
        ), {"t": table, "c": column})
        return rows.first() is not None
    except Exception:  # noqa: BLE001
        # SQLite 等没有 information_schema 的方言: 退化到 inspect
        existing = await _get_existing_columns(conn, table)
        return column in existing


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    """轻量级单表检查"""
    from sqlalchemy import inspect  # noqa: PLC0415
    try:
        rows = await conn.execute(text(
            "SELECT 1 FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "  AND TABLE_NAME = :t LIMIT 1"
        ), {"t": table})
        return rows.first() is not None
    except Exception:  # noqa: BLE001
        def _has(sync_conn: Any) -> bool:
            return inspect(sync_conn).has_table(table)
        return await conn.run_sync(_has)


async def ensure_v2_0_0_schema(
    conn: AsyncConnection, *, verbose: bool = False
) -> dict:
    """
    幂等补齐 v2.0.0 新增列. 在 init_db() create_all 之后调用.

    Args:
        conn: AsyncConnection (必须在 engine.begin() 块内)
        verbose: True 时打印每条 log

    Returns:
        {"added": [str], "skipped": [str], "errors": [str]}
    """
    added: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for table, column, sqltype, default in MIGRATIONS:
        try:
            if not await _table_exists(conn, table):
                msg = f"{table}.{column}: 表不存在 (create_all 会自动建)"
                if verbose:
                    print(f"  [skip] {msg}")
                skipped.append(msg)
                continue
            if await _column_exists(conn, table, column):
                msg = f"{table}.{column}: 已存在"
                if verbose:
                    print(f"  [skip] {msg}")
                skipped.append(msg)
                continue
            # ADD COLUMN
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
            # 索引 (task_type 高频查询)
            if column == "task_type":
                idx_name = f"ix_{table}_{column}"
                try:
                    await conn.execute(text(
                        f"CREATE INDEX `{idx_name}` ON `{table}` (`{column}`)"
                    ))
                except Exception as e:  # noqa: BLE001
                    if verbose:
                        print(f"         (索引 {idx_name} 已存在或建失败, 跳过: {e})")
            msg = f"{table}.{column}"
            if verbose:
                print(f"  [add]  {msg}")
            added.append(msg)
        except Exception as e:  # noqa: BLE001
            # 单列失败不阻塞其他列
            err = f"{table}.{column}: {e!r}"
            errors.append(err)
            if verbose:
                print(f"  [err]  {err}")

    return {"added": added, "skipped": skipped, "errors": errors}
