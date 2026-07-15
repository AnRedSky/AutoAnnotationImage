"""直接看 image 表 + annotation_log 表的真实状态"""
import asyncio
from sqlalchemy import select, func, text
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.models.annotation_log import AnnotationLog

async def main():
    async with AsyncSessionLocal() as db:
        # 全部 image
        imgs_q = await db.execute(text("SELECT COUNT(*) FROM image"))
        total_imgs = imgs_q.scalar()
        anns_q = await db.execute(text("SELECT COUNT(*) FROM annotation_log"))
        total_anns = anns_q.scalar()
        print(f"TOTAL: image={total_imgs}, annotation_log={total_anns}")

        # 全部 annotation_log 的 action 分布
        action_q = await db.execute(text("SELECT action, COUNT(*) FROM annotation_log GROUP BY action"))
        print("annotation_log by action:")
        for row in action_q.all():
            print(f"  {row[0]}: {row[1]}")

        # 最大 image id
        max_q = await db.execute(text("SELECT MAX(id), COUNT(*) FROM image"))
        mx, cnt = max_q.first()
        print(f"image MAX id = {mx}, count = {cnt}")

        # 最新 5 张图
        last_q = await db.execute(text("SELECT id, dataset_id, filename, status FROM image ORDER BY id DESC LIMIT 5"))
        print("latest 5 images:")
        for r in last_q.all():
            print(f"  id={r[0]} dataset={r[1]} {r[2]} status={r[3]}")

        # 最新 5 个 annotation
        last_a = await db.execute(text("SELECT id, image_id, action FROM annotation_log ORDER BY id DESC LIMIT 5"))
        print("latest 5 annotations:")
        for r in last_a.all():
            print(f"  id={r[0]} image={r[1]} action={r[2]}")

        # 哪些 dataset 还有 image
        used_q = await db.execute(text("SELECT dataset_id, COUNT(*) FROM image GROUP BY dataset_id"))
        print("imgs per dataset:")
        for r in used_q.all():
            print(f"  dataset {r[0]}: {r[1]} imgs")

asyncio.run(main())
