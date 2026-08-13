"""
迁移脚本 02: 为 TrainingJob 加 log 字段 (JSON 类型, 存训练日志行)
================================================================
**MIGRATION_ID**: 02
**功能**:
- MySQL 5.7+ 已用 JSON 字段 (training_jobs.history 是 JSON, log 同类型)
- 如果表已存在, 用 ALTER TABLE 添加 log 字段
**幂等**: 重复执行安全 (information_schema.COLUMNS 判定)
**执行顺序**: 02, 必须先于 03/04/05 (后续脚本可能引用 training_jobs 表)
"""
import asyncio
from app.database import engine, Base
from app.models.training_job import TrainingJob
from sqlalchemy import text


MIGRATION_ID = "02"
MIGRATION_DESCRIPTION = "training_jobs 加 log JSON 字段, 存训练日志行"


async def add_log_column():
    async with engine.begin() as conn:
        # 检查字段是否已存在
        check = await conn.execute(text("""
            SELECT COUNT(*) AS c
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'training_jobs'
              AND COLUMN_NAME = 'log'
        """))
        row = check.fetchone()
        if row and row[0] > 0:
            print('[OK] training_jobs.log 已存在, 无需 ALTER')
            return
        # 添加 log JSON 字段
        await conn.execute(text("ALTER TABLE training_jobs ADD COLUMN log JSON NULL"))
        print('[OK] 已为 training_jobs 添加 log 字段')


async def run_migration() -> None:
    """runner 入口: 先确保 SQLAlchemy model 知道这个字段, 再 ALTER 加列"""
    # 先确保 SQLAlchemy model 知道这个字段 (Base.metadata 里)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await add_log_column()
    print(f"[done] [{MIGRATION_ID}] {MIGRATION_DESCRIPTION}")


async def main():
    """兼容历史 CLI 调用 (直接 python migrations/02_add_training_log.py)"""
    await run_migration()


if __name__ == "__main__":
    asyncio.run(main())
