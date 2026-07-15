"""
SSE 端到端验证脚本 - 增量训练 (再训练时自动加载激活模型权重)
"""
import json
import time
import httpx

BASE = "http://127.0.0.1:5000"


def main():
    r = httpx.post(
        f"{BASE}/api/auth/login",
        data={"username": "admin", "password": "admin123"},
        timeout=5,
    )
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}

    # 找一个最近成功的 job (用作「再训练」的源)
    r = httpx.get(
        f"{BASE}/api/training/jobs/",
        params={"page": 1, "page_size": 10, "state": "SUCCESS"},
        headers=H,
    )
    jobs = r.json().get("items", [])
    if not jobs:
        print("No SUCCESS jobs, cannot test retrain")
        return
    src_job = jobs[0]
    print(f"source job #{src_job['id']} model_name={src_job['model_name']} state={src_job['state']}")

    # 验证激活模型
    r = httpx.get(f"{BASE}/api/models/", headers=H)
    models = r.json().get("items", [])
    active = [m for m in models if m.get("is_active")]
    if not active:
        print("No active model, activating the source's model")
        # 找与 src_job 同 dataset_id 的模型
        for m in models:
            if m.get("dataset_id") == src_job["dataset_id"] and m.get("name") == src_job["model_name"]:
                r = httpx.post(f"{BASE}/api/models/{m['id']}/activate", headers=H, timeout=10)
                print(f"  activate result: {r.json()}")
                break

    # 触发再训练 (mode=restart), 后端会自动用当前激活的模型作为增量起点
    r = httpx.post(
        f"{BASE}/api/training/jobs/{src_job['id']}/start",
        params={"mode": "restart"},
        json={
            "dataset_id": src_job["dataset_id"],
            "base_model": src_job["base_model"],
            "model_name": src_job["model_name"],
            "epochs": 1,
            "batch_size": 8,
            "learning_rate": 1e-3,
        },
        headers=H,
        timeout=30,
    )
    print(f"retrain start status={r.status_code} body={r.text[:200]}")

    # 新 job 的 task_id 在 message 里 (restart 模式不返回新的 task_id, 实际是后端 re-deliver)
    # 直接读 job list 拿到新 PENDING
    time.sleep(1)
    r = httpx.get(
        f"{BASE}/api/training/jobs/",
        params={"page": 1, "page_size": 5, "state": "PENDING"},
        headers=H,
    )
    new_jobs = r.json().get("items", [])
    if not new_jobs:
        print("No PENDING job found, check PROGRESS")
        r = httpx.get(
            f"{BASE}/api/training/jobs/",
            params={"page": 1, "page_size": 5, "state": "PROGRESS"},
            headers=H,
        )
        new_jobs = r.json().get("items", [])
    if not new_jobs:
        print("Cannot find new job, exit")
        return
    new_job = new_jobs[0]
    new_id = new_job["id"]
    new_celery_id = new_job["celery_task_id"]
    print(f"new job #{new_id} celery_task_id={new_celery_id}")

    # 连 SSE
    url = f"{BASE}/api/training/progress/stream/{new_celery_id}?token={token}"
    print("Connecting to SSE for retrain...")
    with httpx.stream("GET", url, timeout=30) as response:
        print(f"SSE status={response.status_code}")
        data_stats_seen = False
        incremental_seen = False
        count = 0
        start_time = time.time()
        for line in response.iter_lines():
            elapsed = time.time() - start_time
            if elapsed > 30:
                break
            if line:
                print(f"  [{elapsed:5.1f}s] {line[:200]}")
            if not line or not line.startswith("data: "):
                continue
            try:
                d = json.loads(line[6:])
            except Exception as e:
                print(f"  JSON parse error: {e}, line={line!r}")
                continue
            count += 1
            if "data_total" in d and not data_stats_seen:
                data_stats_seen = True
                print("\n>>> DATA STATS in retrain SSE payload:")
                print(json.dumps({k: d.get(k) for k in (
                    "data_total", "data_train", "data_val",
                    "num_classes", "class_names",
                )}, ensure_ascii=False, indent=2))
            if "pretrained_loaded" in d and not incremental_seen:
                incremental_seen = True
                print("\n>>> INCREMENTAL TRAINING state in retrain SSE payload:")
                print(json.dumps({k: d.get(k) for k in (
                    "pretrained_loaded", "pretrained_path", "pretrained_error",
                )}, ensure_ascii=False, indent=2))
            if d.get("state") in ("SUCCESS", "FAILURE", "REVOKED"):
                print(f"\n>>> Terminal state: {d['state']}, breaking loop")
                break
    print(f"\nRead {count} data events in {time.time() - start_time:.1f}s")
    print(f"data_stats_seen={data_stats_seen}, incremental_seen={incremental_seen}")


if __name__ == "__main__":
    main()
