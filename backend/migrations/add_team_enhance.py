"""团队管理增强迁移 (v3.3.1)
==========================

新增字段:
1. team.tenant_id              (INT NULL)              — v3.2 多租户兼容预留
2. team.archived_at            (DATETIME NULL)         — 软删除标记 (Phase L3 使用)
3. team_member.invited_by_id   (INT NULL)              — 邀请溯源
4. annotation_log.team_id      (INT NULL)              — 团队级统计 (Phase L2 团队统计用)

新增索引:
- ix_team_archived_at          (team.archived_at)
- ix_team_member_invited_by    (team_member.invited_by_id)
- ix_annotation_log_team       (annotation_log.team_id)

数据回填:
- annotation_log.team_id ← image → dataset.team_id (反查)

幂等: 重复执行安全 (information_schema 检查)
跨 DB 兼容: MySQL 5.7+ / SQLite (CREATE INDEX 错误被静默吞)
"""
import asyncio
from sqlalchemy import text
from app.database import engine, Base
# 触发 SQLAlchemy metadata 注册 (新建字段后必须让 Base 知道)
from app.tasks.model.team import Team  # noqa: F401
from app.tasks.model.team_member import TeamMember  # noqa: F401
from app.tasks.model.annotation_log import AnnotationLog  # noqa: F401
from app.tasks.model.dataset import Dataset  # noqa: F401
from app.tasks.model.image import Image  # noqa: F401


# 字段名 -> 字段定义 (与 ORM Column 类型严格一致)
# 注意: 所有字段都加 NULL 默认, 不破坏已有数据
NEW_COLUMNS = [
    ("team", "tenant_id", "INT NULL"),
    ("team", "archived_at", "DATETIME NULL"),
    ("team_member", "invited_by_id", "INT NULL"),
    ("annotation_log", "team_id", "INT NULL"),
]


# 索引名 -> (table, column)
NEW_INDEXES = [
    ("ix_team_archived_at", "team", "archived_at"),
    ("ix_team_member_invited_by", "team_member", "invited_by_id"),
    ("ix_annotation_log_team", "annotation_log", "team_id"),
]


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


async def _index_exists(conn, table: str, index_name: str) -> bool:
    """检查索引是否存在 (仅 MySQL, SQLite 跳过)"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        return False
    result = await conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table
          AND INDEX_NAME = :index_name
    """), {"table": table, "index_name": index_name})
    return (result.scalar() or 0) > 0


async def _backfill_annotation_log_team(conn) -> int:
    """回填 annotation_log.team_id ← image → dataset.team_id

    逻辑: 对于已存在的 annotation_log 记录, 通过 image -> dataset 链路反查 team_id
    性能: 单 UPDATE JOIN, 走索引, 数据量 < 100k 时 < 1s
    """
    dialect = conn.dialect.name

    if dialect == "sqlite":
        # SQLite: 通过子查询实现
        sql = text("""
            UPDATE annotation_log
            SET team_id = (
                SELECT d.team_id
                FROM image i
                JOIN dataset d ON d.id = i.dataset_id
                WHERE i.id = annotation_log.image_id
            )
            WHERE team_id IS NULL
        """)
    else:
        # MySQL: 直接 JOIN UPDATE
        sql = text("""
            UPDATE annotation_log al
            JOIN image i ON i.id = al.image_id
            JOIN dataset d ON d.id = i.dataset_id
            SET al.team_id = d.team_id
            WHERE al.team_id IS NULL
        """)

    result = await conn.execute(sql)
    # SQLite 返回 rowcount
    return result.rowcount or 0


async def add_team_enhance():
    async with engine.begin() as conn:
        # 1) 加新列
        for table, col_name, col_def in NEW_COLUMNS:
            if await _column_exists(conn, table, col_name):
                print(f"[OK] {table}.{col_name} 已存在, 跳过")
                continue
            stmt = f"ALTER TABLE `{table}` ADD COLUMN `{col_name}` {col_def}"
            await conn.execute(text(stmt))
            print(f"[OK] 已添加 {table}.{col_name} ({col_def})")

        # 2) 加新索引
        for idx_name, table, col_name in NEW_INDEXES:
            if await _index_exists(conn, table, idx_name):
                print(f"[OK] 索引 {idx_name} 已存在, 跳过")
                continue
            try:
                stmt = f"CREATE INDEX `{idx_name}` ON `{table}` (`{col_name}`)"
                await conn.execute(text(stmt))
                print(f"[OK] 已创建索引 {idx_name} ON {table}({col_name})")
            except Exception as e:
                # SQLite 重复创建会被拒, 静默跳过
                msg = str(e).lower()
                if "already exists" in msg or "duplicate" in msg:
                    print(f"[SKIP] 索引 {idx_name} (重复创建)")
                else:
                    raise

        # 3) 回填 annotation_log.team_id
        print("\n[BACKFILL] 回填 annotation_log.team_id ...")
        updated = await _backfill_annotation_log_team(conn)
        print(f"[BACKFILL] annotation_log.team_id 回填完成, 更新 {updated} 条")


async def main():
    # 让 Base.metadata 知道新字段, 后续 create_all 也兼容
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await add_team_enhance()
    print("\n[done] 团队管理增强迁移完成")
    print("- team.tenant_id (v3.2 多租户兼容)")
    print("- team.archived_at (软删除标记)")
    print("- team_member.invited_by_id (邀请溯源)")
    print("- annotation_log.team_id (团队级统计)")
    print("- 3 个新索引")


if __name__ == "__main__":
    asyncio.run(main())
