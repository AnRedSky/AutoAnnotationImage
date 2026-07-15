"""Check latest training jobs and their errors"""
import asyncio
from app.database import AsyncSessionLocal
from app.models.training_job import TrainingJob
from sqlalchemy import select, desc


async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(TrainingJob).order_by(desc(TrainingJob.id)).limit(5)
        )).scalars().all()
        for r in rows:
            err = (r.error or '')[:200]
            print(f'#{r.id} state={r.state} progress={r.progress} model={r.model_name}')
            if err:
                print(f'   error: {err}')


asyncio.run(main())
