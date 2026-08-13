"""
迁移脚本 06: 回填历史 TrainingJob.pretrain_mode = 'from_scratch' (NULL → '微调')
============================================================================
**MIGRATION_ID**: 06
**功能**:
- 历史 NULL 行 → 统一设为 'from_scratch' (前端展示为「微调」)
**幂等**:
- 仅更新 pretrain_mode IS NULL 的行
- 已存在的 from_scratch / incremental / resume 不会被覆盖
- 重复执行输出 "已处理 0 行", 不抛错
**执行顺序**: 06, 强依赖 05 (pretrain_mode 字段必须先建)
**依赖关系**: 05_add_training_pretrain_mode
"""
import asyncio
from sqlalchemy import text

from app.database import engine


MIGRATION_ID = "06"
MIGRATION_DESCRIPTION = "回填历史 training_jobs.pretrain_mode = 'from_scratch' (NULL → from_scratch)"


async def backfill_null_pretrain_mode():
    """把所有 pretrain_mode IS NULL 的历史任务标为 from_scratch (展示为「微调」)"""
    async with engine.begin() as conn:
        # 1) 统计 NULL 行数 (便于 dry-run / 日志)
        cnt_row = await conn.execute(
            text("SELECT COUNT(*) AS c FROM training_jobs WHERE pretrain_mode IS NULL")
        )
        null_count = cnt_row.fetchone()[0]
        print(f"[INFO] 当前 pretrain_mode IS NULL 的历史任务数: {null_count}")

        if null_count == 0:
            print("[OK] 无需回填 (历史 NULL 任务已全部处理)")
            return 0

        # 2) 回填: 仅更新 NULL 行
        result = await conn.execute(
            text(
                "UPDATE training_jobs "
                "SET pretrain_mode = 'from_scratch' "
                "WHERE pretrain_mode IS NULL"
            )
        )
        # SQLAlchemy 2.x Result.rowcount 反映实际受影响行数
        affected = result.rowcount or 0
        print(f"[OK] 已将 {affected} 条历史任务的 pretrain_mode 设为 'from_scratch' (前端展示「微调」)")
        return affected


async def run_migration() -> None:
    """runner 入口"""
    affected = await backfill_null_pretrain_mode()
    print(f"[done] [{MIGRATION_ID}] {MIGRATION_DESCRIPTION} (affected={affected})")


async def main():
    """兼容历史 CLI 调用"""
    await run_migration()


if __name__ == "__main__":
    asyncio.run(main())
