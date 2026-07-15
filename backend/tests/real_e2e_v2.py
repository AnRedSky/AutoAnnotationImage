"""
Real E2E test: login -> start training -> poll progress -> verify result.
Reports honestly what works and what doesn't.
"""
import asyncio
import sys
import time
import json
import httpx
from pathlib import Path

BASE = "http://127.0.0.1:5000"
DS_ID = None  # filled from API

def log(tag, msg):
    print(f"[{tag}] {msg}", flush=True)

def fail(msg):
    log("FAIL", msg)
    sys.exit(1)

def ok(msg):
    log("OK", msg)

def main():
    global DS_ID
    log("START", "=== Real E2E Training Test ===")

    # 1) Health check
    try:
        r = httpx.get(f"{BASE}/api/health", timeout=5)
        ok(f"GET /api/health -> {r.status_code} {r.text[:200]}")
    except Exception as e:
        fail(f"health unreachable: {e}")

    # 2) Login (OAuth2 form-encoded)
    r = httpx.post(f"{BASE}/api/auth/login",
                   data={"username": "admin", "password": "admin123"},
                   timeout=10)
    if r.status_code != 200:
        fail(f"login failed: {r.status_code} {r.text[:300]}")
    token = r.json().get("access_token")
    if not token:
        fail(f"no access_token in login response: {r.text[:300]}")
    ok(f"login OK, token len={len(token)}")

    H = {"Authorization": f"Bearer {token}"}

    # 3) List datasets
    r = httpx.get(f"{BASE}/api/datasets", headers=H, timeout=10)
    if r.status_code != 200:
        fail(f"datasets list failed: {r.status_code} {r.text[:300]}")
    ds_list = r.json()
    items = ds_list.get("items", ds_list) if isinstance(ds_list, dict) else ds_list
    log("INFO", f"datasets count={len(items)}")
    if not items:
        fail("no datasets available")
    DS_ID = items[0]["id"]
    ok(f"using dataset id={DS_ID} name={items[0].get('name')}")

    # 4) Start training (short epochs to keep test quick)
    r = httpx.post(
        f"{BASE}/api/training/start",
        params={
            "dataset_id": DS_ID,
            "base_model": "resnet18",
            "model_name": "v1",
            "epochs": 2,
            "batch_size": 8,
            "learning_rate": 1e-3,
        },
        headers=H,
        timeout=30,
    )
    if r.status_code not in (200, 202):
        fail(f"start training failed: {r.status_code} {r.text[:500]}")
    data = r.json()
    task_id = data.get("task_id") or data.get("celery_task_id")
    if not task_id:
        fail(f"no task_id in start response: {data}")
    ok(f"training started, task_id={task_id}")

    # 5) Poll progress
    log("POLL", "polling progress every 2s for up to 120s")
    states_seen = []
    progress_max = 0.0
    final_state = None
    for i in range(60):
        time.sleep(2)
        try:
            r = httpx.get(f"{BASE}/api/training/progress/{task_id}", timeout=5)
        except Exception as e:
            log("WARN", f"poll #{i} error: {e}")
            continue
        if r.status_code != 200:
            log("WARN", f"poll #{i} status {r.status_code}: {r.text[:200]}")
            continue
        d = r.json()
        st = d.get("state", "?")
        pg = d.get("progress", 0)
        msg = d.get("message", "")
        if st not in states_seen:
            states_seen.append(st)
            log("STATE", f"new state: {st}")
        if pg > progress_max:
            progress_max = pg
        print(f"  poll #{i:02d} state={st:10s} progress={pg:6.2f}%  msg={msg[:80]}", flush=True)
        if st in ("SUCCESS", "FAILURE", "REVOKED"):
            final_state = st
            break

    if not final_state:
        fail(f"training did not finish in 120s; last state={states_seen} max_progress={progress_max}")

    # 6) Get final progress one more time to confirm
    r = httpx.get(f"{BASE}/api/training/progress/{task_id}", timeout=5)
    final = r.json()
    log("FINAL", f"state={final_state} progress={final.get('progress')} msg={final.get('message')}")

    # 7) Check history endpoint
    r = httpx.get(f"{BASE}/api/training/history/{task_id}", timeout=5)
    hist = r.json() if r.status_code == 200 else {"history": []}
    log("HIST", f"history length={len(hist.get('history', []))}")

    # 8) Check jobs list
    r = httpx.get(f"{BASE}/api/training/jobs", headers=H, timeout=10)
    jobs = r.json() if r.status_code == 200 else []
    log("JOBS", f"jobs count={len(jobs)}")
    for j in jobs[:5]:
        log("JOB", f"  id={j.get('id')} state={j.get('state')} progress={j.get('progress')} model_name={j.get('model_name')}")

    # 9) Verdict
    if final_state == "SUCCESS":
        ok("=== TEST PASSED (training reported SUCCESS) ===")
        sys.exit(0)
    elif final_state == "FAILURE":
        log("FAILURE_INFO", f"msg={final.get('message')}")
        # try to get error detail
        for j in jobs:
            if j.get("celery_task_id") == task_id:
                eid = j.get("id")
                r = httpx.post(f"{BASE}/api/training/jobs/{eid}/error", headers=H, timeout=5)
                if r.status_code == 200:
                    log("ERR", f"  {r.text[:500]}")
                break
        sys.exit(2)
    else:
        fail(f"unexpected terminal state: {final_state}")

if __name__ == "__main__":
    main()
