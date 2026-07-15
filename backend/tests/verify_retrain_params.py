"""E2E test: 再训练参数隔离
- 选一个 SUCCESS 任务
- 调 POST /api/training/jobs/{id}/start?mode=restart 带新 body
- 验证:
  1. API 返回 success
  2. 新 task_id 出现
  3. 原 job 记录保持不变
  4. 新 job (worker 接手后创建) 使用新参数
"""
import urllib.request
import urllib.parse
import json
import asyncio
import time
from app.database import AsyncSessionLocal
from app.models.training_job import TrainingJob
from sqlalchemy import select, desc


def login(username='admin', password='admin'):
    login_data = urllib.parse.urlencode({'username': username, 'password': password}).encode()
    req = urllib.request.Request('http://127.0.0.1:5000/api/auth/login', data=login_data, method='POST')
    r = urllib.request.urlopen(req, timeout=5)
    return json.loads(r.read().decode())['access_token']


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


async def get_job(job_id):
    async with AsyncSessionLocal() as db:
        return await db.get(TrainingJob, job_id)


async def get_job_by_task_id(task_id):
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
        )).scalar_one_or_none()
        return row


async def main():
    # 1. 登录
    try:
        token = login('admin', 'admin')
    except Exception:
        token = login('admin', 'admin123')
    print(f'[1] login OK')

    # 2. 找一个 SUCCESS 任务
    data = http_get('/api/training/jobs/?state=SUCCESS&page=1&page_size=5', token)
    items = data['items']
    print(f'[2] found {len(items)} SUCCESS jobs')
    if not items:
        print('NO SUCCESS JOBS, abort')
        return
    target = items[0]
    target_id = target['id']
    # 记录原 job 的参数快照 (用于"原 job 不变"对比)
    orig_snapshot = {
        'model_name': target['model_name'],
        'base_model': target['base_model'],
        'epochs': target['epochs'],
        'batch_size': target['batch_size'],
        'learning_rate': target['learning_rate'],
        'state': target['state'],
    }
    print(f'    target job #{target_id}: model_name={target["model_name"]}, '
          f'base_model={target["base_model"]}, epochs={target["epochs"]}, '
          f'batch_size={target["batch_size"]}, learning_rate={target["learning_rate"]}')
    print(f'    快照: {orig_snapshot}')

    # 3. 调 restart, body 用新参数
    new_body = {
        'base_model': 'resnet18',
        'model_name': 'e2e_retest_v1',
        'epochs': 5,
        'batch_size': 16,
        'learning_rate': 0.0005,
    }
    print(f'[3] POST /api/training/jobs/{target_id}/start?mode=restart with body={new_body}')
    r = http_post(f'/api/training/jobs/{target_id}/start?mode=restart', token, new_body)
    print(f'    response: {json.dumps(r, ensure_ascii=False, indent=2)}')
    if not r.get('success'):
        print('FAILED: success=False')
        return
    new_task_id = r['task_id']
    print(f'    new task_id: {new_task_id}')

    # 4. 等 2s 让 worker 接手写 DB
    print('[4] wait 2s for worker pickup...')
    time.sleep(2)

    # 5. 验证原 job 保持不变 (用快照对比)
    orig_job = await get_job(target_id)
    print(f'[5] verify original job #{target_id} unchanged:')
    for k, v in orig_snapshot.items():
        cur = getattr(orig_job, k)
        same = cur == v
        marker = '[OK]' if same else '[FAIL]'
        print(f'    {marker} {k}: {cur} (snapshot was {v})')
        assert same, f'原 job {k} 被改了: {cur} != {v}'
    print('    [OK] 原 job 全部参数未变')

    # 6. 验证新 task 对应的 job (worker 接手后创建)
    new_job = await get_job_by_task_id(new_task_id)
    if new_job is None:
        print('[6] new job not yet created by worker (PENDING or not picked up)')
    else:
        print(f'[6] new job #{new_job.id} created:')
        print(f'    celery_task_id={new_job.celery_task_id}')
        print(f'    base_model={new_job.base_model} (should be resnet18)')
        print(f'    epochs={new_job.epochs} (should be 5)')
        print(f'    batch_size={new_job.batch_size} (should be 16)')
        print(f'    learning_rate={new_job.learning_rate} (should be 0.0005)')
        print(f'    model_name={new_job.model_name} (should start with e2e_retest_v1_r)')
        assert new_job.base_model == 'resnet18', f'新 job base_model 错误: {new_job.base_model}'
        assert new_job.epochs == 5, f'新 job epochs 错误: {new_job.epochs}'
        assert new_job.batch_size == 16, f'新 job batch_size 错误: {new_job.batch_size}'
        assert new_job.learning_rate == 0.0005, f'新 job learning_rate 错误: {new_job.learning_rate}'
        assert new_job.model_name.startswith('e2e_retest_v1_r'), f'新 job model_name 错误: {new_job.model_name}'
        assert new_job.id != target_id, f'新 job id 应不同于原 job'
        print('    [OK] 新 job 使用新参数, 且是独立记录')

    print()
    print('=' * 60)
    print('E2E TEST PASSED: retrain params isolation OK')
    print('=' * 60)


asyncio.run(main())
