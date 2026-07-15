"""端到端验证训练 + 设备记录:
1. 给 dataset 26 几张图标 human_confirmed (因为 dataset 26 都是 ai_labeled, 训练会报 'Too few labeled')
2. 启动训练 (CPU 模式, 快速, 1 epoch 试水)
3. 轮询 wait_for SUCCESS
4. 查 /api/training/jobs/{id} 验证 device_type / device_info / gpu_peak_memory_mb 写库
"""
import asyncio
import json
import sys
import time

import httpx
from sqlalchemy import update, select

sys.path.insert(0, "backend")
from app.database import AsyncSessionLocal  # noqa
from app.models.image import Image  # noqa
from app.models.category import Category  # noqa


BASE = "http://127.0.0.1:5000"


async def mark_human_confirmed(dataset_id: int, n: int = 3):
    async with AsyncSessionLocal() as db:
        # 先看 dataset 状态
        from sqlalchemy import func
        ai_count = (await db.execute(
            select(func.count(Image.id)).where(
                Image.dataset_id == dataset_id, Image.status == "ai_labeled"
            )
        )).scalar()
        pending_count = (await db.execute(
            select(func.count(Image.id)).where(
                Image.dataset_id == dataset_id, Image.status == "pending"
            )
        )).scalar()
        print(f"  [ds#{dataset_id}] ai_labeled={ai_count} pending={pending_count}")
        # 找前 n 张 ai_labeled OR pending
        stmt = (
            select(Image)
            .where(
                Image.dataset_id == dataset_id,
                Image.status.in_(["ai_labeled", "pending"]),
            )
            .limit(n)
        )
        rows = (await db.execute(stmt)).scalars().all()
        if not rows:
            print("[WARN] 没有 ai_labeled/pending 图可标记")
            return 0
        # 取第一个 category
        first_cat = (await db.execute(
            select(Category).where(Category.dataset_id == dataset_id).limit(1)
        )).scalars().first()
        if not first_cat:
            print("[WARN] dataset 无 category")
            return 0
        for img in rows:
            img.final_label_id = first_cat.id
            img.status = "human_confirmed"
        await db.commit()
        print(f"[OK] 已标 {len(rows)} 张图 human_confirmed (label_id={first_cat.id})")
        return len(rows)


def get_token():
    r = httpx.post(f"{BASE}/api/auth/login", data={"username": "admin", "password": "admin123"})
    return r.json()["access_token"]


def start_training(token: str, dataset_id: int, epochs: int = 1, batch_size: int = 4):
    r = httpx.post(
        f"{BASE}/api/training/start",
        params={
            "dataset_id": dataset_id,
            "base_model": "resnet18",
            "model_name": "",
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": 0.001,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    print(f"[start] status={r.status_code} body={r.text[:200]}")
    return r.json()["task_id"]


def get_progress(token: str, task_id: str):
    r = httpx.get(
        f"{BASE}/api/training/progress/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    return r.json()


def get_job(token: str, job_id: int):
    r = httpx.get(
        f"{BASE}/api/training/jobs/{job_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    return r.json()


def main():
    # 1. 标几张图
    n = asyncio.run(mark_human_confirmed(30, 3))  # ds#30 室内室外数据集
    if n == 0:
        print("[ABORT] 没标到图, 退出")
        return
    # 2. 登录 + 启动训练
    token = get_token()
    task_id = start_training(token, 30, epochs=1, batch_size=4)
    print(f"[task_id] {task_id}")
    # 3. 轮询
    job_id = None
    for i in range(60):
        time.sleep(3)
        p = get_progress(token, task_id)
        st = p.get("state")
        pr = p.get("progress", 0)
        msg = p.get("message", "")
        print(f"  [{i:02d}] state={st} progress={pr:.1f}% msg={msg[:80]}")
        # 找到 job_id (前端轮询 progress 没返 job_id, 用 /api/training/jobs 查)
        if job_id is None and st != "PENDING":
            r = httpx.get(
                f"{BASE}/api/training/jobs?state={st}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
            items = r.json().get("items", [])
            for it in items:
                if it.get("celery_task_id") == task_id:
                    job_id = it["id"]
                    print(f"  [found job_id={job_id}]")
                    break
        if st in ("SUCCESS", "FAILURE", "REVOKED"):
            break
    # 4. 取 job 详情, 验证 device info
    if job_id:
        job = get_job(token, job_id)
        print("\n=== TrainingJob 详情 ===")
        print(f"  state: {job.get('state')}")
        print(f"  device_type: {job.get('device_type')}")
        print(f"  device_name: {job.get('device_name')}")
        di = job.get("device_info") or {}
        print(f"  device_info keys: {list(di.keys())}")
        print(f"  device_info.cuda_version: {di.get('cuda_version')}")
        print(f"  device_info.cpu_count: {di.get('cpu_count')}")
        print(f"  device_info.ram_gb: {di.get('ram_gb')}")
        print(f"  device_info.torch_version: {di.get('torch_version')}")
        print(f"  device_info.python_version: {di.get('python_version')}")
        print(f"  device_info.os_platform: {di.get('os_platform')}")
        print(f"  gpu_peak_memory_mb: {job.get('gpu_peak_memory_mb')}")


if __name__ == "__main__":
    main()
