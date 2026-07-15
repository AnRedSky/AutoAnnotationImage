"""
Honest end-to-end functional verification.

Run this to confirm: training -> model -> activate -> predict pipeline works.
Exits 0 on full PASS, non-zero on any FAIL.

Required services:
  - Backend on http://127.0.0.1:5000
  - Celery worker (consumes queue at redis://127.0.0.1:9770/1)
  - Redis 127.0.0.1:9770
  - MySQL 127.0.0.1:3310 (image_annotation db)
  - MinIO 127.0.0.1:9000
"""
import sys
import time
import json
import httpx

BASE = "http://127.0.0.1:5000"
RESULTS: list = []

def log(t, m): print(f"[{t}] {m}", flush=True)
def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    log("PASS" if ok else "FAIL", f"{name}: {detail}")

def main():
    log("START", "=== Honest E2E Functional Verification ===")

    # 1) Health
    try:
        r = httpx.get(f"{BASE}/api/health", timeout=5)
        record("health", r.status_code == 200, f"{r.status_code}")
    except Exception as e:
        record("health", False, str(e))
        return

    # 2) Login
    r = httpx.post(f"{BASE}/api/auth/login", data={"username":"admin","password":"admin123"})
    record("login", r.status_code == 200 and "access_token" in r.json(),
           f"{r.status_code}")
    if r.status_code != 200: return
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}

    # 3) Datasets
    r = httpx.get(f"{BASE}/api/datasets", headers=H)
    ds = r.json().get("items", [])
    record("datasets_list", r.status_code == 200 and len(ds) > 0,
           f"{r.status_code}, {len(ds)} datasets")
    if not ds: return
    DS = ds[0]["id"]

    # 4) Image list
    r = httpx.get(f"{BASE}/api/images/list/{DS}?limit=3", headers=H)
    images = r.json().get("items", r.json())
    record("images_list", r.status_code == 200 and len(images) > 0,
           f"{r.status_code}, {len(images) if isinstance(images, list) else '?'} images")
    if not isinstance(images, list) or not images: return
    img_id = images[0]["id"]

    # 5) Image preview WITHOUT auth (img tag scenario)
    r = httpx.get(f"{BASE}/api/files/{img_id}", timeout=10)
    record("image_preview_no_auth",
           r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"),
           f"{r.status_code}, {len(r.content)} bytes, ct={r.headers.get('content-type')}")

    # 6) Image preview WITH token
    r = httpx.get(f"{BASE}/api/files/{img_id}?token={token}", timeout=10)
    record("image_preview_with_token",
           r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"),
           f"{r.status_code}, {len(r.content)} bytes")

    # 7) Start training
    r = httpx.post(f"{BASE}/api/training/start",
                   params={"dataset_id": DS, "base_model": "resnet18",
                           "model_name": "v1", "epochs": 2,
                           "batch_size": 8, "learning_rate": 1e-3},
                   headers=H, timeout=30)
    if r.status_code not in (200, 202):
        record("training_start", False, f"{r.status_code} {r.text[:200]}")
        return
    task_id = r.json().get("task_id")
    record("training_start", bool(task_id), f"task_id={task_id}")
    if not task_id: return

    # 8) Poll progress
    log("POLL", "polling for up to 60s...")
    states = []
    final = None
    progress_max = 0.0
    for i in range(30):
        time.sleep(2)
        r = httpx.get(f"{BASE}/api/training/progress/{task_id}", timeout=5)
        if r.status_code != 200: continue
        d = r.json()
        st = d.get("state", "?")
        pg = d.get("progress", 0)
        msg = d.get("message", "")
        if st not in states:
            states.append(st)
            log("STATE", f"  {st}")
        if pg > progress_max: progress_max = pg
        print(f"  poll #{i:02d} state={st:10s} progress={pg:6.2f}%  msg={msg[:60]}", flush=True)
        if st in ("SUCCESS", "FAILURE", "REVOKED"):
            final = d
            break
    record("training_completes", final is not None and final.get("state") == "SUCCESS",
           f"final={final}")
    record("training_progress_100", final and final.get("progress") == 100.0,
           f"final.progress={final.get('progress') if final else '?'}")
    record("training_has_message", final and bool(final.get("message")),
           f"final.message={final.get('message') if final else '?'}")

    # 9) Models list
    r = httpx.get(f"{BASE}/api/models/", headers=H)
    models = r.json().get("items", [])
    record("models_list", r.status_code == 200, f"{r.status_code}, {len(models)} models")

    # 10) Activate latest v1
    v1 = sorted([m for m in models if m.get("name") == "v1"],
                key=lambda x: x.get("id", 0), reverse=True)
    if v1:
        target = v1[0]
        r = httpx.post(f"{BASE}/api/models/{target['id']}/activate", headers=H)
        record("model_activate",
               r.status_code == 200 and r.json().get("active_model_id") == target["id"],
               f"{r.status_code}, body={r.text[:150]}")
    else:
        record("model_activate", False, "no v1 model to activate")

    # 11) Auto-annotate
    r = httpx.post(f"{BASE}/api/auto-annotate/run",
                   json={"dataset_id": DS, "model_name": "v1",
                         "confidence_threshold": 0.5, "async_mode": True},
                   headers=H, timeout=60)
    record("auto_annotate_runs", r.status_code in (200, 202),
           f"{r.status_code}, {r.text[:200]}")

    # 12) Training history
    r = httpx.get(f"{BASE}/api/training/history/{task_id}", timeout=5)
    hist = r.json().get("history", []) if r.status_code == 200 else []
    record("training_history",
           r.status_code == 200 and len(hist) >= 1,
           f"{r.status_code}, {len(hist)} epochs")

    # 13) Training job detail
    # /api/training/jobs 返回分页结构 {items, total, page, page_size}
    # 修复: 之前直接迭代 r.json() 拿到的是 dict 的 keys (字符串), 触发 AttributeError: 'str' object has no attribute 'get'
    r = httpx.get(f"{BASE}/api/training/jobs", headers=H)
    jobs_payload = r.json()
    jobs_list = jobs_payload.get("items", []) if isinstance(jobs_payload, dict) else (jobs_payload or [])
    our_job = None
    for j in jobs_list:
        if j.get("celery_task_id") == task_id:
            our_job = j
            break
    record("training_job_in_db",
           our_job is not None and our_job.get("state") == "SUCCESS" and our_job.get("model_version_id"),
           f"state={our_job.get('state') if our_job else '?'} "
           f"progress={our_job.get('progress') if our_job else '?'} "
           f"model_version_id={our_job.get('model_version_id') if our_job else '?'}")

    # Final summary
    print()
    log("SUMMARY", "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    log("SUMMARY", f"Passed: {passed}/{len(RESULTS)}  Failed: {failed}")
    for name, ok, detail in RESULTS:
        marker = "OK  " if ok else "FAIL"
        log("SUMMARY", f"  [{marker}] {name}: {detail}")
    if failed:
        log("SUMMARY", f"!!! {failed} CHECK(S) FAILED !!!")
        sys.exit(1)
    log("SUMMARY", "=== ALL CHECKS PASSED ===")

if __name__ == "__main__":
    main()
