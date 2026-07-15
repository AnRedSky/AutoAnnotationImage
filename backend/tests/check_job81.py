"""Drill into job #81 to understand which dataset and timing."""
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.training_job import TrainingJob
from app.models.image import Image

async def main():
    async with AsyncSessionLocal() as db:
        j = await db.get(TrainingJob, 81)
        print(f'job #81: state={j.state} error={j.error!r}')
        print(f'  dataset_id={j.dataset_id}, base_model={j.base_model}, model_name={j.model_name}')
        print(f'  started_at={j.started_at}, finished_at={j.finished_at}')
        # Count labeled images for this dataset
        from sqlalchemy import func as sa_func
        cnt_total = (await db.execute(
            select(sa_func.count(Image.id)).where(Image.dataset_id == j.dataset_id)
        )).scalar_one()
        cnt_labeled = (await db.execute(
            select(sa_func.count(Image.id)).where(
                Image.dataset_id == j.dataset_id,
                Image.status.in_(['human_confirmed', 'human_corrected', 'ai_labeled'])
            )
        )).scalar_one()
        print(f'  dataset {j.dataset_id}: {cnt_total} total images, {cnt_labeled} in trainable status')

asyncio.run(main())
