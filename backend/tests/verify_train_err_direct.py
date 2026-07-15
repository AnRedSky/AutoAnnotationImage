"""直接调 run_training, 验证新错误信息"""
import asyncio
import concurrent.futures
from app.ml.train import run_training
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.models.training_job import TrainingJob
from sqlalchemy import select, update


async def setup_empty_dataset():
    """临时把所有图片 status 改为 'pending' (DB 允许的最小状态), 让训练返回新错误"""
    async with AsyncSessionLocal() as db:
        # 保存原状态
        result = await db.execute(select(Image).where(Image.dataset_id == 28))
        images = result.scalars().all()
        original_statuses = [(img.id, img.status) for img in images]
        # 改状态为 pending
        for img in images:
            img.status = 'pending'
        await db.commit()
        return original_statuses


async def restore_statuses(originals):
    """还原图片状态"""
    async with AsyncSessionLocal() as db:
        for img_id, status in originals:
            await db.execute(
                update(Image).where(Image.id == img_id).values(status=status)
            )
        await db.commit()


def run_training_in_thread():
    """在独立线程中跑 run_training (避免事件循环冲突)"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(
            run_training,
            dataset_id=28,
            base_model='resnet18',
            model_name='err_test',
            epochs=1,
            batch_size=2,
        )
        return future.result(timeout=60)


async def main():
    print('[1] 保存原图片状态, 临时清空...')
    originals = await setup_empty_dataset()
    print(f'    saved {len(originals)} image statuses, set to pending')

    try:
        print('[2] 在子线程中调用 run_training, 应该抛新错误...')
        try:
            result = run_training_in_thread()
            print(f'    [UNEXPECTED] result: {result}')
        except ValueError as e:
            err_msg = str(e)
            print(f'    [OK] ValueError raised (len={len(err_msg)}):')
            print(f'    {err_msg[:500]}')
            if '需 ≥2' in err_msg or '>= 2' in err_msg:
                print('    [PASS] 错误信息包含新阈值提示「需 ≥2」 — train.py 已是新代码')
            elif 'need >= 10' in err_msg:
                print('    [FAIL] 错误信息还是旧的 need >= 10 — train.py 仍是旧代码!')
            else:
                print('    [WARN] 未知错误信息格式')
        except Exception as e:
            print(f'    [ERR] 抛了非 ValueError 异常: {type(e).__name__}: {e}')
    finally:
        print('[3] 还原图片状态...')
        await restore_statuses(originals)
        print('    done')


asyncio.run(main())
