"""
迁移脚本 05: 为 TrainingJob 加预训练追溯字段
==========================================
**MIGRATION_ID**: 05
**功能**:
- pretrain_mode + pretrain_source_mv_id
- v3.0.0: 用户点「再训练」时, 记录该任务是「增量训练 / 从头训练 / 继续暂停」
- 模式与 add_training_device_info.py 一致: 检查 information_schema, 已存在跳过
- 2 个字段都是新加, 全部 NULL 兼容, 不破坏已有数据
**幂等**: 重复执行安全 (information_schema 判定)
**执行顺序**: 05, 依赖 02 (training_jobs 表已存在)
**依赖关系**: 02_add_training_log, 04_add_training_device_info
**被依赖**: 06_backfill_pretrain_mode_finetune (必须先建字段才能回填)
"""
import asyncio
from app.database import engine, Base
# v3.0.0 拆分后: TrainingJob 已迁到 app.tasks.model, 同步触发 metadata 注册
from app.tasks.model.training_job import TrainingJob  # noqa: F401
from sqlalchemy import text


MIGRATION_ID = "05"
MIGRATION_DESCRIPTION = "training_jobs 加 pretrain_mode/pretrain_source_mv_id 字段, 加 pretrain_mode 索引"


# 字段名 -> 字段定义 (与 ORM Column 类型严格一致)
# 注意: 这两个字段都加 index, pretrain_mode 用普通 BTree 索引 (按模式筛选)
# pretrain_source_mv_id 不加索引 (只在详情展示用, 不会按来源 MV 批量查)
NEW_COLUMNS = [
    ("pretrain_mode", "VARCHAR(16) NULL"),
    ("pretrain_source_mv_id", "INT NULL"),
]

# 索引: pretrain_mode 单列索引 (按模式筛选任务)
NEW_INDEXES = [
    ("ix_training_jobs_pretrain_mode", "pretrain_mode"),
]


async def _column_exists(conn, table: str, column: str) -> bool:
    row = await conn.execute(
        text(
            "SELECT COUNT(*) AS c "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "  AND TABLE_NAME = :table "
            "  AND COLUMN_NAME = :column"
        ),
        {"table": table, "column": column},
    )
    cnt = row.fetchone()[0]
    return cnt > 0


async def _index_exists(conn, table: str, index_name: str) -> bool:
    row = await conn.execute(
        text(
            "SELECT COUNT(*) AS c "
            "FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "  AND TABLE_NAME = :table "
            "  AND INDEX_NAME = :index"
        ),
        {"table": table, "index": index_name},
    )
    cnt = row.fetchone()[0]
    return cnt > 0


async def add_pretrain_columns():
    async with engine.begin() as conn:
        for col_name, col_def in NEW_COLUMNS:
            if await _column_exists(conn, "training_jobs", col_name):
                print(f"[OK] training_jobs.{col_name} 已存在, 跳过")
                continue
            stmt = f"ALTER TABLE training_jobs ADD COLUMN `{col_name}` {col_def}"
            await conn.execute(text(stmt))
            print(f"[OK] 已添加 training_jobs.{col_name} ({col_def})")

        for idx_name, col_name in NEW_INDEXES:
            if await _index_exists(conn, "training_jobs", idx_name):
                print(f"[OK] training_jobs.{idx_name} 索引已存在, 跳过")
                continue
            stmt = f"CREATE INDEX `{idx_name}` ON training_jobs (`{col_name}`)"
            await conn.execute(text(stmt))
            print(f"[OK] 已创建 training_jobs.{idx_name} 索引")


async def run_migration() -> None:
    """runner 入口"""
    # 让 Base.metadata 知道新字段, 后续 create_all 也兼容
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await add_pretrain_columns()
    print(f"[done] [{MIGRATION_ID}] {MIGRATION_DESCRIPTION}")


async def main():
    """兼容历史 CLI 调用"""
    await run_migration()


if __name__ == "__main__":
    asyncio.run(main())
