"""回填历史 TrainingJob.pretrain_mode = 'from_scratch' (NULL → '微调')

背景:
- v3.0.0 新增 pretrain_mode 字段, 迁移前 (add_training_pretrain_mode.py 跑之前)
  创建的 TrainingJob 记录, pretrain_mode 是 NULL
- 业务侧要求: 历史的训练任务, 训练模式统一默认为「微调」(NULL → 'from_scratch')
- 'from_scratch' 在 DB 中保留英文, 前端 label 展示为「微调」(见 training_job.PRETRAIN_MODE_LABELS)

幂等:
- 仅更新 pretrain_mode IS NULL 的行
- 已存在的 from_scratch / incremental / resume 不会被覆盖
- 重复执行输出 "已处理 0 行", 不抛错

执行: python migrations/backfill_pretrain_mode_finetune.py
"""
import asyncio
from sqlalchemy import text

from app.database import engine


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


async def main():
    affected = await backfill_null_pretrain_mode()
    print(f"\n[done] 历史 NULL 任务回填完成 (affected={affected})")


if __name__ == "__main__":
    asyncio.run(main())
