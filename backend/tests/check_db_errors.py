"""Check recent training jobs and their error state."""
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.training_job import TrainingJob


async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(TrainingJob).order_by(TrainingJob.id.desc()).limit(8)
        )).scalars().all()
        for r in rows:
            print(f'  job #{r.id:3d} state={r.state:8s} progress={r.progress:6} '
                  f'task_id={r.celery_task_id[:18] if r.celery_task_id else "(none)":18s}...')
            if r.error:
                print(f'    DB.error: {r.error[:200]}')


asyncio.run(main())
