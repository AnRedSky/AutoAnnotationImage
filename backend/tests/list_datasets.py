"""列出所有数据集的图片数和已标注数"""
import asyncio
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.models.dataset import Dataset
from sqlalchemy import select, func


async def main():
    async with AsyncSessionLocal() as db:
        datasets = (await db.execute(select(Dataset))).scalars().all()
        for ds in datasets:
            total = (await db.execute(
                select(func.count(Image.id)).where(Image.dataset_id == ds.id)
            )).scalar_one()
            labeled = (await db.execute(
                select(func.count(Image.id))
                .where(
                    Image.dataset_id == ds.id,
                    Image.status.in_(["human_confirmed", "human_corrected", "ai_labeled"])
                )
            )).scalar_one()
            print(f'  ds_id={ds.id} name={ds.name!r:30s} total={total} labeled={labeled}')


asyncio.run(main())
