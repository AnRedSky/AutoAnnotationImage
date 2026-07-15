"""
SSE 端到端验证脚本 - 数据集统计 + 增量训练字段 (httpx + 详细调试)
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

    # 启动训练 (用较多 epoch, 给 SSE 足够时间)
    r = httpx.post(
        f"{BASE}/api/training/start",
        params={
            "dataset_id": 29,
            "base_model": "resnet18",
            "model_name": "sse_stats_check_v3",
            "epochs": 5,
            "batch_size": 8,
            "learning_rate": 1e-3,
        },
        headers=H,
        timeout=30,
    )
    print(f"start status={r.status_code}")
    task_id = r.json().get("task_id")
    print(f"task_id={task_id}")

    # 立即连 SSE
    url = f"{BASE}/api/training/progress/stream/{task_id}?token={token}"
    print("Connecting to SSE...")
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
            # 打印每行 (truncated)
            if line:
                print(f"  [{elapsed:5.1f}s] {line[:150]}")
            if not line or not line.startswith("data: "):
                continue
            try:
                d = json.loads(line[6:])
            except Exception as e:
                print(f"  JSON parse error: {e}, line={line!r}")
                continue
            count += 1
            has_stats = "data_total" in d
            has_incremental = "pretrained_loaded" in d
            if has_stats and not data_stats_seen:
                data_stats_seen = True
                print("\n>>> DATA STATS in SSE payload:")
                stats = {k: d.get(k) for k in (
                    "data_total", "data_train", "data_val",
                    "num_classes", "class_names",
                )}
                print(json.dumps(stats, ensure_ascii=False, indent=2))
            if has_incremental and not incremental_seen:
                incremental_seen = True
                print("\n>>> INCREMENTAL TRAINING state in SSE payload:")
                inc = {k: d.get(k) for k in (
                    "pretrained_loaded", "pretrained_path", "pretrained_error",
                )}
                print(json.dumps(inc, ensure_ascii=False, indent=2))
            if d.get("state") in ("SUCCESS", "FAILURE", "REVOKED"):
                print(f"\n>>> Terminal state: {d['state']}, breaking loop")
                break
    print(f"\nRead {count} data events in {time.time() - start_time:.1f}s")
    print(f"data_stats_seen={data_stats_seen}, incremental_seen={incremental_seen}")


if __name__ == "__main__":
    main()
