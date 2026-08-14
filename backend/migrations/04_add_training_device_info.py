"""
迁移脚本 04: 为 TrainingJob 加训练资源字段
=========================================
**MIGRATION_ID**: 04
**功能**:
- device_type / device_name / device_info / gpu_peak_memory_mb
- 模式与 add_training_log.py 一致: 检查 information_schema, 已存在跳过
- 4 个字段都是新加, 全部 NULL 兼容, 不破坏已有数据
**幂等**: 重复执行安全 (information_schema 判定)
**执行顺序**: 04, 依赖 02 (training_jobs 表已存在)
**依赖关系**: 02_add_training_log
"""
import asyncio
from app.database import engine, Base
from app.tasks.model.training_job import TrainingJob
from sqlalchemy import text


MIGRATION_ID = "04"
MIGRATION_DESCRIPTION = "training_jobs 加训练资源字段 (device_type/device_name/device_info/gpu_peak_memory_mb)"


# 字段名 -> 字段定义 (与 ORM Column 类型严格一致)
# 注意: device_info / gpu_peak_memory_mb 不可加 UNIQUE (历史 NULL 不冲突)
# MySQL 5.7+ ADD COLUMN 用 ADD COLUMN, VARCHAR(16) / VARCHAR(128) / JSON / INT
NEW_COLUMNS = [
    ("device_type", "VARCHAR(16) NULL"),
    ("device_name", "VARCHAR(128) NULL"),
    ("device_info", "JSON NULL"),
    ("gpu_peak_memory_mb", "INT NULL"),
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


async def add_device_columns():
    async with engine.begin() as conn:
        for col_name, col_def in NEW_COLUMNS:
            if await _column_exists(conn, "training_jobs", col_name):
                print(f"[OK] training_jobs.{col_name} 已存在, 跳过")
                continue
            stmt = f"ALTER TABLE training_jobs ADD COLUMN `{col_name}` {col_def}"
            await conn.execute(text(stmt))
            print(f"[OK] 已添加 training_jobs.{col_name} ({col_def})")


async def run_migration() -> None:
    """runner 入口"""
    # 让 Base.metadata 知道新字段, 后续 create_all 也兼容
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await add_device_columns()
    print(f"[done] [{MIGRATION_ID}] {MIGRATION_DESCRIPTION}")


async def main():
    """兼容历史 CLI 调用"""
    await run_migration()


if __name__ == "__main__":
    asyncio.run(main())
