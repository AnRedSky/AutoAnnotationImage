"""触发新错误: 找一个没标注图片的数据集, 启动训练, 验证新错误信息"""
import urllib.request
import urllib.parse
import json
import asyncio
import time
from app.database import AsyncSessionLocal
from app.models.training_job import TrainingJob
from app.models.image import Image
from sqlalchemy import select, desc, func


def login(username='admin', password='admin'):
    login_data = urllib.parse.urlencode({'username': username, 'password': password}).encode()
    req = urllib.request.Request('http://127.0.0.1:5000/api/auth/login', data=login_data, method='POST')
    try:
        r = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError:
        return None
    return json.loads(r.read().decode()).get('access_token')


def get_token():
    for pwd in ['admin', 'admin123', '123456', 'password']:
        t = login('admin', pwd)
        if t:
            return t
    raise RuntimeError('cannot login')


def http_get(path, token):
    req = urllib.request.Request(f'http://127.0.0.1:5000{path}', headers={'Authorization': f'Bearer {token}'})
    r = urllib.request.urlopen(req, timeout=5)
    return json.loads(r.read().decode())


def http_post(path, token, body=None):
    data = json.dumps(body).encode() if body else b''
    req = urllib.request.Request(
        f'http://127.0.0.1:5000{path}',
        data=data,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST'
    )
    r = urllib.request.urlopen(req, timeout=10)
    return json.loads(r.read().decode())


async def find_empty_dataset():
    """找有图片但没有 ai_labeled/human_confirmed/human_corrected 状态的数据集"""
    async with AsyncSessionLocal() as db:
        # 列出所有 dataset
        from app.models.dataset import Dataset
        datasets = (await db.execute(select(Dataset))).scalars().all()
        for ds in datasets:
            labeled_count = (await db.execute(
                select(func.count(Image.id))
                .where(
                    Image.dataset_id == ds.id,
                    Image.status.in_(["human_confirmed", "human_corrected", "ai_labeled"])
                )
            )).scalar_one()
            if labeled_count == 0:
                total = (await db.execute(
                    select(func.count(Image.id)).where(Image.dataset_id == ds.id)
                )).scalar_one()
                if total > 0:
                    return ds.id, ds.name, total
        return None, None, None


async def main():
    token = get_token()

    # 1. 找有图片但无标注的数据集
    ds_id, ds_name, total = await find_empty_dataset()
    if ds_id is None:
        print('NO empty dataset found')
        return
    print(f'[1] found empty dataset: id={ds_id} name={ds_name} total_images={total}')

    # 2. 启动训练 (会触发新错误信息)
    new_body = {
        'dataset_id': ds_id,
        'base_model': 'resnet18',
        'model_name': f'new_err_test_{int(time.time())}',
        'epochs': 2,
        'batch_size': 4,
        'learning_rate': 0.0005,
    }
    r = http_post('/api/training/start', token, new_body)
    print(f'[2] start response: {json.dumps(r, ensure_ascii=False, indent=2)}')
    task_id = r['task_id']
    print(f'    new task_id: {task_id}')

    # 3. 等 5s 让 worker 处理
    print('[3] wait 5s for worker...')
    time.sleep(5)

    # 4. 查 DB 中该任务
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalar_one_or_none()
        if row is None:
            print('job not found in DB')
            return
        print(f'[4] job state={row.state} progress={row.progress} error={row.error}')

        if row.error:
            if '需 ≥2' in row.error or '>= 2' in row.error:
                print('    [OK] 新错误信息包含 「需 ≥2」 提示')
            elif 'need >= 10' in row.error:
                print('    [FAIL] 错误信息还是旧的 need >= 10 — worker 还在用旧代码!')
            else:
                print('    [WARN] 未知错误信息格式')


asyncio.run(main())
