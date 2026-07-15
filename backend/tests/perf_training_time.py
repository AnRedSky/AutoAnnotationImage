"""
Performance Test: Training Latency
==================================
5 类 × 200 张 × 5 epoch CPU 训练, 统计总耗时
"""
import asyncio
import time
from pathlib import Path

import httpx


API_BASE = "http://127.0.0.1:8000"
USERNAME = "admin"
PASSWORD = "admin123"
DATASET_ID = 1
EPOCHS = 5
BATCH_SIZE = 16
LR = 1e-4
BASE_MODEL = "efficientnet_b0"


async def main():
    async with httpx.AsyncClient() as client:
        # 1) 登录
        login = await client.post(
            f"{API_BASE}/api/auth/login",
            data={"username": USERNAME, "password": PASSWORD},
        )
        if login.status_code != 200:
            print(f"[fail] login failed: {login.status_code}")
            return
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2) 启动训练
        print(f"[start] model={BASE_MODEL} epochs={EPOCHS} batch_size={BATCH_SIZE}")
        t0 = time.perf_counter()
        resp = await client.post(
            f"{API_BASE}/api/training/start",
            headers=headers,
            params={
                "dataset_id": DATASET_ID,
                "base_model": BASE_MODEL,
                "model_name": f"perf_{int(t0)}",
                "epochs": EPOCHS,
                "batch_size": BATCH_SIZE,
                "learning_rate": LR,
            },
        )
        if resp.status_code not in (200, 201):
            print(f"[fail] start: {resp.status_code} {resp.text}")
            return
        task_id = resp.json()["task_id"]
        print(f"[ok] task_id={task_id}")

        # 3) 轮询进度
        last_progress = -1
        while True:
            await asyncio.sleep(5)
            pr = await client.get(f"{API_BASE}/api/training/progress/{task_id}", headers=headers)
            if pr.status_code != 200:
                print(f"[warn] progress query failed: {pr.status_code}")
                continue
            data = pr.json()
            progress = data.get("progress", 0)
            state = data.get("state")
            if progress != last_progress:
                print(f"  [{state}] {progress}% | {data.get('message', '')[:80]}")
                last_progress = progress
            if state in ("SUCCESS", "FAILURE"):
                break
            if time.perf_counter() - t0 > 60 * 60:  # 1h hard timeout
                print("[timeout] aborting")
                break

        total = time.perf_counter() - t0

        # 4) 写结果
        results_dir = Path("tests/results")
        results_dir.mkdir(parents=True, exist_ok=True)
        out_path = results_dir / "perf_training_time.txt"
        out_path.write_text(
            f"Base model: {BASE_MODEL}\n"
            f"Epochs: {EPOCHS}\n"
            f"Batch size: {BATCH_SIZE}\n"
            f"LR: {LR}\n"
            f"Total seconds: {total:.2f}\n"
            f"Per epoch: {total / EPOCHS:.2f}\n"
            f"Final state: {state}\n",
            encoding="utf-8",
        )
        print(f"\n[done] total {total:.1f}s ({total / EPOCHS:.1f}s/epoch)")
        print(f"[saved] {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
