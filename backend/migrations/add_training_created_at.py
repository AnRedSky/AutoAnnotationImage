"""为 TrainingJob 加 created_at 字段 (任务入库时间)
- 与 started_at 区分: created_at 是 API 入库瞬间, started_at 是 worker 接手
- ALTER TABLE 加字段, 已有行用 NOW() 兜底 (近似值, 仅历史回填用)
- 同时给 created_at 加索引, 方便后续按创建时间倒序/分页
"""
import asyncio
from app.database import engine
from sqlalchemy import text


async def add_created_at_column():
    async with engine.begin() as conn:
        # 1) 检查字段是否已存在
        check = await conn.execute(text("""
            SELECT COUNT(*) AS c
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'training_jobs'
              AND COLUMN_NAME = 'created_at'
        """))
        row = check.fetchone()
        if row and row[0] > 0:
            print('[OK] training_jobs.created_at 已存在, 无需 ALTER')
        else:
            # 2) 添加 created_at DATETIME 字段 (NOT NULL, 默认 NOW() 兜底历史行)
            await conn.execute(text("""
                ALTER TABLE training_jobs
                ADD COLUMN created_at DATETIME NOT NULL
                DEFAULT CURRENT_TIMESTAMP
            """))
            print('[OK] 已为 training_jobs 添加 created_at 字段')

        # 3) 历史行: 把 started_at 回填到 created_at (PENDING 阶段没 started_at,
        # 但 PROGRESS 起的行有). 用 UPDATE 而不是靠 ALTER DEFAULT 推断, 精确.
        # engine.begin() 自动 commit, 注意: 不要在本次 block 退出前 dispose, 否则
        # aiomysql 会在 GC 阶段触发 ROLLBACK, 之前的 UPDATE 会丢
        await conn.execute(text("""
            UPDATE training_jobs
            SET created_at = COALESCE(started_at, finished_at, NOW())
            WHERE started_at IS NOT NULL OR finished_at IS NOT NULL
        """))
        print('[OK] 已用 started_at/finished_at 回填历史行的 created_at')

        # 4) 给 created_at 加索引 (按创建时间排序/筛选用)
        idx_check = await conn.execute(text("""
            SELECT COUNT(*) AS c
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'training_jobs'
              AND INDEX_NAME = 'ix_training_jobs_created_at'
        """))
        idx_row = idx_check.fetchone()
        if idx_row and idx_row[0] > 0:
            print('[OK] training_jobs.created_at 索引已存在')
        else:
            await conn.execute(text("""
                CREATE INDEX ix_training_jobs_created_at
                ON training_jobs (created_at)
            """))
            print('[OK] 已为 training_jobs.created_at 加索引')
    # engine.begin() 上下文退出时自动 commit, 整段原子完成


async def main():
    await add_created_at_column()


if __name__ == '__main__':
    asyncio.run(main())
