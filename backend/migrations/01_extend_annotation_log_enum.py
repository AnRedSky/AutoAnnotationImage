"""
迁移脚本 01: v2.5.15 P0-2 迁移 - 扩展 annotation_log 表
====================================================
**MIGRATION_ID**: 01
**功能**:
1. action 枚举: 增加 auto_annotate_pretrained / auto_annotate_finetuned
2. 新增 payload JSON 字段: 存额外审计信息 (模型名/box数 等)

**幂等**: 重复执行安全 (用 information_schema.COLUMNS 判定)

**注意 (v3.4.0 维护)**:
- 本脚本的 new_values 仅含 v2.5.15 当初扩展的 2 个值;
  v3.0.0 增 mark_unqualified / unmark_unqualified, v3.4.0 增 revert_to_ai
  均不在这里维护, 走 app/database/migration.py:ensure_annotation_log_action_enum
  (启动 init_db 自动执行, 幂等补齐, 跨所有环境生效).
- 若本脚本直接运行 (脱离 init_db), 只对未跑过 v3.0.0 的环境补上 auto_annotate_*,
  其他扩展将由数据库迁移系统后续接管.

**用法**:
    cd backend
    python -m migrations.01_extend_annotation_log_enum
    # 或
    python migrations/01_extend_annotation_log_enum.py

**执行顺序**: 01, 依赖 00 (annotation_log 表已建)
"""
import asyncio
from sqlalchemy import text
from app.database import engine, Base
from app.tasks.model.annotation_log import AnnotationLog  # noqa: F401  触发 SQLAlchemy metadata 注册


MIGRATION_ID = "01"
MIGRATION_DESCRIPTION = "v2.5.15 P0-2: annotation_log 加 payload JSON, action ENUM 扩展 (auto_annotate_pretrained/auto_annotate_finetuned)"


async def _column_exists(conn, table: str, column: str) -> bool:
    """检查列是否存在 (兼容 MySQL / SQLite)"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        cols = [row[1] for row in result.fetchall()]
        return column in cols
    # MySQL / 其他
    result = await conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table
          AND COLUMN_NAME = :column
    """), {"table": table, "column": column})
    return (result.scalar() or 0) > 0


async def _enum_has_value(conn, table: str, column: str, value: str) -> bool:
    """检查 MySQL ENUM 列是否包含指定值 (仅 MySQL, SQLite 不支持 ENUM 类型)"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        # SQLite 不支持 ENUM, SQLAlchemy 用 CHECK 约束实现
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        for row in result.fetchall():
            # row: cid, name, type, notnull, dflt_value, pk
            col_type = (row[2] or "").lower()
            if row[1] == column and value.lower() in col_type:
                return True
        return False
    # MySQL: 查 COLUMN_TYPE
    result = await conn.execute(text("""
        SELECT COLUMN_TYPE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table
          AND COLUMN_NAME = :column
    """), {"table": table, "column": column})
    row = result.fetchone()
    if not row:
        return False
    col_type = (row[0] or "").upper()
    return value in col_type


async def run_migration():
    async with engine.begin() as conn:
        # 1. 先建所有新列 (create_all 只建不存在的)
        await conn.run_sync(Base.metadata.create_all)
        print("[OK] create_all: annotation_log.payload 已就位 (如新装)")

        # 2. 已有表加 payload 列 (如数据库已有 annotation_log 但没 payload)
        if not await _column_exists(conn, "annotation_log", "payload"):
            dialect = conn.dialect.name
            if dialect == "sqlite":
                # SQLite JSON 实际是 TEXT, 兼容存储
                await conn.execute(text("ALTER TABLE annotation_log ADD COLUMN payload TEXT NULL"))
            else:
                # MySQL 5.7+ 支持 JSON 原生类型
                await conn.execute(text("ALTER TABLE annotation_log ADD COLUMN payload JSON NULL"))
            print("[OK] ALTER TABLE annotation_log ADD COLUMN payload")
        else:
            print("[SKIP] annotation_log.payload 已存在")

        # 3. 扩展 action ENUM (兼容已存在的所有枚举值 + 旧数据)
        # 注意: 必须使用完整的 9 个目标值 (含 v3.0.0 的 mark/unmark_unqualified 和 v3.4.0 的 revert_to_ai),
        # 否则数据库中已有这些值的记录会触发 MySQL "Data truncated for column 'action'" 错误.
        all_target_values = (
            "'ai_predict', 'confirm', 'correct', 'reject',"
            "'auto_annotate_pretrained', 'auto_annotate_finetuned',"
            "'mark_unqualified', 'unmark_unqualified',"
            "'revert_to_ai'"
        )
        if not await _enum_has_value(conn, "annotation_log", "action", "auto_annotate_pretrained"):
            dialect = conn.dialect.name
            if dialect == "sqlite":
                # SQLite 不支持直接修改 CHECK, 跳过 (测试环境用 create_all 即可)
                print("[SKIP] SQLite ENUM 修改受限, 测试环境依赖 create_all")
            else:
                # MySQL: ALTER COLUMN 扩展 ENUM (使用完整枚举列表, 避免截断已有数据)
                await conn.execute(
                    text(f"ALTER TABLE annotation_log MODIFY COLUMN action ENUM({all_target_values}) NOT NULL")
                )
                print(f"[OK] ALTER TABLE annotation_log MODIFY COLUMN action ENUM (使用完整 9 个枚举值)")
        else:
            print("[SKIP] annotation_log.action 已包含 auto_annotate_pretrained")

    print(f"\n[done] [{MIGRATION_ID}] {MIGRATION_DESCRIPTION}")


async def main():
    await run_migration()
    print("\n=== v2.5.15 P0-2 迁移完成 ===")
    print("- annotation_log.action 枚举扩展 (ai_predict/confirm/correct/reject + auto_annotate_*)")
    print("- annotation_log.payload JSON 字段 (存模型名/box数 等审计上下文)")


if __name__ == "__main__":
    asyncio.run(main())
