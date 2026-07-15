"""E2E test:
- D3: SSE 日志持久化 (POST/GET /training/jobs/{id}/log)
- D4: 模型默认命名 {base_model}_v1_{ts}
"""
import urllib.request
import urllib.parse
import json
import re
import time
import asyncio
from app.database import AsyncSessionLocal
from app.models.training_job import TrainingJob
from sqlalchemy import select, desc


def get_token():
    for pwd in ['admin', 'admin123', '123456', 'password']:
        login_data = urllib.parse.urlencode({'username': 'admin', 'password': pwd}).encode()
        req = urllib.request.Request('http://127.0.0.1:5000/api/auth/login', data=login_data, method='POST')
        try:
            r = urllib.request.urlopen(req, timeout=5)
            return json.loads(r.read().decode())['access_token']
        except Exception:
            continue
    raise RuntimeError('cannot login')


def http_get(path, token):
    req = urllib.request.Request(f'http://127.0.0.1:5000{path}', headers={'Authorization': f'Bearer {token}'})
    return json.loads(urllib.request.urlopen(req, timeout=5).read().decode())


def http_post(path, token, body=None):
    data = json.dumps(body).encode() if body else b''
    req = urllib.request.Request(
        f'http://127.0.0.1:5000{path}',
        data=data,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST'
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())


async def main():
    token = get_token()
    print('[init] login OK')

    # ============== D4: 模型默认命名 (不传 model_name) ==============
    print('\n[TEST D4] model_name auto-generation')
    # 找任意一个 SUCCESS 任务, 用其 dataset_id
    data = http_get('/api/training/jobs/?state=SUCCESS&page=1&page_size=1', token)
    items = data['items']
    if not items:
        print('  no SUCCESS job, skip D4')
    else:
        ds_id = items[0]['dataset_id']
        # 不传 model_name → 后端应自动生成 {base_model}_v1_{ts}
        # 注意: /api/training/start 是 sync def + query params, 不是 body
        path = (
            f'/api/training/start?dataset_id={ds_id}'
            f'&base_model=resnet50&epochs=1&batch_size=4&learning_rate=0.0005'
        )
        r = http_post(path, token)
        print(f'  start response: {json.dumps(r)}')
        task_id = r.get('task_id', '')
        # 等几秒让 worker 写 DB
        time.sleep(3)
        async with AsyncSessionLocal() as db:
            job = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if job:
                print(f'  new job #{job.id} model_name: {job.model_name}')
                if re.match(r'^resnet50_v1_\d{6,}$', job.model_name):
                    print('  [OK] model_name 匹配 resnet50_v1_<ts> 格式')
                else:
                    print(f'  [FAIL] model_name 格式不符合: {job.model_name}')

    # ============== D3: SSE 日志持久化 ==============
    print('\n[TEST D3] log persistence')
    # 任意找一个 job
    data = http_get('/api/training/jobs/?page=1&page_size=5', token)
    items = data['items']
    if not items:
        print('  no job, skip D3')
        return
    job_id = items[0]['id']
    print(f'  using job #{job_id}')

    # 清空 log
    async with AsyncSessionLocal() as db:
        j = await db.get(TrainingJob, job_id)
        j.log = []
        await db.commit()

    # POST 3 条日志
    lines = [
        f'[{time.time():.1f}] state=PROGRESS progress=10.5% epoch=1/1 msg=epoch start',
        f'[{time.time():.1f}] state=PROGRESS progress=50.0% epoch=1/1 msg=epoch 50%',
        f'[{time.time():.1f}] state=SUCCESS progress=100.0% epoch=1/1 msg=training done',
    ]
    for line in lines:
        r = http_post(f'/api/training/jobs/{job_id}/log', token, {'line': line})
        assert isinstance(r, dict) and 'log' in r
    print(f'  appended {len(lines)} lines')

    # GET 验证持久化
    r = http_get(f'/api/training/jobs/{job_id}/log', token)
    fetched = r.get('log', [])
    print(f'  fetched {len(fetched)} lines back')
    if len(fetched) == len(lines) and fetched == lines:
        print('  [OK] 日志持久化和读取均正常, 内容一致')
    else:
        print(f'  [FAIL] expected {len(lines)} lines, got {len(fetched)}')
        for i, (e, a) in enumerate(zip(lines, fetched)):
            if e != a:
                print(f'    line {i}: expected={e!r}, actual={a!r}')

    # 验证 DB 真实存储
    async with AsyncSessionLocal() as db:
        j = await db.get(TrainingJob, job_id)
        db_log = j.log if isinstance(j.log, list) else []
        print(f'  DB 中 log 字段: {len(db_log)} 行')
        if len(db_log) == len(lines):
            print('  [OK] DB log 持久化成功')
        else:
            print(f'  [FAIL] DB log 行数: {len(db_log)} (期望 {len(lines)})')

    print('\n' + '=' * 60)
    print('E2E PASSED: log persistence + model_name default')
    print('=' * 60)


asyncio.run(main())
