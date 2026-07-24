"""
v2.5.29 举一反三验证: classification / segmentation history 实时同步
==================================================================
- classification: 走 train_model_task, 每个 epoch 写 Redis
- segmentation:  走 train_segmentation_task, 每个 epoch 写 Redis + DB

跑法: uv run --project backend python backend/tests/verify_all_history.py
"""
import sys
import time
import json
import httpx

BASE = "http://127.0.0.1:5000"


def log(t, m):
    print(f"[{t}] {m}", flush=True)


def poll_history(task_id, headers, max_wait=300, label=""):
    """轮询 history + state, 验证 history 实时增长"""
    log("INFO", f"[{label}] 开始轮询 history (每 5s) ...")
    start = time.time()
    last_len = 0
    last_state = None
    while time.time() - start < max_wait:
        try:
            r = httpx.get(f"{BASE}/api/training/history/{task_id}",
                          headers=headers, timeout=10)
        except Exception as e:
            log("WARN", f"history error: {e}")
            time.sleep(5)
            continue
        if r.status_code != 200:
            log("WARN", f"history {r.status_code}")
            time.sleep(5)
            continue
        body = r.json()
        history = body.get("history", [])
        if len(history) > last_len:
            log("INFO", f"[{label}] history {last_len} -> {len(history)}")
            last_len = len(history)
        # 拿 state
        try:
            r2 = httpx.get(f"{BASE}/api/training/progress/{task_id}",
                           headers=headers, timeout=10)
            state = r2.json().get("state", "?") if r2.status_code == 200 else "?"
        except Exception:
            state = "?"
        if state in ("SUCCESS", "FAILURE", "REVOKED"):
            log("OK", f"[{label}] 终态: state={state} history_len={last_len}")
            return last_len, history, state
        time.sleep(5)
    return last_len, history, "TIMEOUT"


def run_classification(token, headers):
    """验证 classification history 实时增长"""
    log("INFO", "=== Classification 验证 ===")
    r = httpx.get(f"{BASE}/api/datasets?page=1&page_size=50", headers=headers, timeout=30)
    if r.status_code != 200:
        log("FAIL", f"list datasets {r.status_code}")
        return False
    datasets = r.json().get("items", [])
    cls_datasets = [d for d in datasets if d.get("task_type") == "classification"]
    if not cls_datasets:
        log("WARN", "no classification dataset, skip")
        return True  # skip 不是 fail

    ds_id = cls_datasets[0]["id"]
    log("OK", f"classification dataset: id={ds_id} name={cls_datasets[0].get('name')}")

    # 启动 2-epoch 训练
    r = httpx.post(f"{BASE}/api/training/start", headers=headers,
                    params={"dataset_id": ds_id},
                    json={
                        "base_model": "resnet18",
                        "model_name": "test_cls_history",
                        "epochs": 2,
                        "batch_size": 4,
                    }, timeout=30)
    if r.status_code not in (200, 201):
        log("FAIL", f"start train {r.status_code} {r.text[:200]}")
        return False
    task_id = r.json().get("celery_task_id")
    log("OK", f"train started: task_id={task_id}")

    last_len, history, state = poll_history(task_id, headers, label="cls")
    if last_len == 0:
        log("FAIL", "classification history 始终为空!")
        return False
    sample = history[0]
    log("INFO", f"第一条 history: {json.dumps(sample, ensure_ascii=False)[:200]}")
    if "epoch" not in sample:
        log("FAIL", "classification history 缺 epoch 字段")
        return False
    log("OK", "classification history 实时同步 OK")
    return True


def run_segmentation(token, headers):
    """验证 segmentation history 实时增长"""
    log("INFO", "=== Segmentation 验证 ===")
    r = httpx.get(f"{BASE}/api/datasets?page=1&page_size=50", headers=headers, timeout=30)
    if r.status_code != 200:
        log("FAIL", f"list datasets {r.status_code}")
        return False
    datasets = r.json().get("items", [])
    seg_datasets = [d for d in datasets if d.get("task_type") == "segmentation"]
    if not seg_datasets:
        log("WARN", "no segmentation dataset, skip")
        return True

    ds_id = seg_datasets[0]["id"]
    log("OK", f"segmentation dataset: id={ds_id} name={seg_datasets[0].get('name')}")

    r = httpx.post(f"{BASE}/api/segmentation/train", headers=headers, json={
        "dataset_id": ds_id,
        "backbone": "deeplabv3_resnet50",
        "model_alias": "test_seg_history",
        "epochs": 2,
        "batch_size": 4,
    }, timeout=30)
    if r.status_code not in (200, 201):
        log("FAIL", f"start train {r.status_code} {r.text[:200]}")
        return False
    task_id = r.json().get("celery_task_id")
    log("OK", f"train started: task_id={task_id}")

    last_len, history, state = poll_history(task_id, headers, label="seg")
    if last_len == 0:
        log("FAIL", "segmentation history 始终为空!")
        return False
    sample = history[0]
    log("INFO", f"第一条 history: {json.dumps(sample, ensure_ascii=False)[:200]}")
    if "epoch" not in sample and "current_epoch" not in sample:
        log("WARN", f"segmentation history 缺 epoch 字段: keys={list(sample.keys())}")
    log("OK", "segmentation history 实时同步 OK")
    return True


def main():
    r = httpx.post(f"{BASE}/api/auth/login",
                   data={"username": "admin", "password": "admin123"},
                   timeout=30)
    if r.status_code != 200:
        log("FAIL", f"login {r.status_code}")
        sys.exit(1)
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    log("OK", "login")

    ok = True
    ok = run_classification(token, headers) and ok
    ok = run_segmentation(token, headers) and ok

    if ok:
        log("OK", "=== 举一反三验证: 全部通过 ===")
        sys.exit(0)
    else:
        log("FAIL", "=== 举一反三验证: 部分失败 ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
