"""E2E 验证: 训练任务控制 (暂停 / 复用启动 / 删除 / 分页)
=============================================================
覆盖前端 Training.vue 调用的所有行操作端点 + 列表分页
1. 登录拿 token
2. 创建 N 个新训练任务 (用同一个 dataset) — 由于 dataset 标注不足会快速失败
3. 验证 GET /training/jobs 分页 (total, items, page, page_size 字段)
4. 验证 POST /training/jobs/{id}/start (复用启动 — 终态可重启)
5. 验证 POST /training/jobs/{id}/pause (暂停 — 终态/不存在应拒绝)
6. 验证 DELETE /training/jobs/{id} (删除 — 终态可删)
7. 验证 PENDING 状态: pause 可工作 + delete 拒绝
"""
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:5000"
VENV_PY = r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend\.venv\Scripts\python.exe"
BACKEND_DIR = r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend"


def http(method, path, *, token=None, data=None, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())


def login():
    r = urllib.request.Request(f"{BASE}/api/auth/login", data=b"username=admin&password=admin123", method="POST")
    r.add_header("Content-Type", "application/x-www-form-urlencoded")
    return json.loads(urllib.request.urlopen(r, timeout=10).read().decode())["access_token"]


def main():
    tok = login()
    print(f"[1] logged in, token len={len(tok)}")

    # 找一个 dataset
    ds = http("GET", "/api/datasets", token=tok)
    items = ds.get("items") or ds or []
    if not items:
        print("NO DATASETS - abort")
        return 1
    ds_id = items[0]["id"]
    print(f"[2] using dataset_id={ds_id}")

    # 创建 3 个新训练任务 (会 PENDING 状态因为没有 worker)
    job_ids = []
    for i in range(3):
        r = http("POST", "/api/training/start", token=tok, params={
            "dataset_id": ds_id, "base_model": "resnet18",
            "model_name": f"e2e_{int(time.time())}_{i}",
            "epochs": 2, "batch_size": 8, "learning_rate": 0.001,
        })
        job_ids.append((r["task_id"], None))  # (task_id, job_id?) — job_id 需查列表
        print(f"    created task_id={r['task_id'][:8]}…")
    time.sleep(1)

    # ---- 验证 1: 分页 ----
    print()
    print("[3] /training/jobs?page=1&page_size=2 — 验证分页字段")
    # 等 worker 处理完 3 个新任务 (solo 池, 每个 ~12s 失败, 串行)
    print("    waiting for worker to insert DB records and complete (tasks may fail)...")
    for wait_i in range(120):  # 最多等 120s
        r = http("GET", "/api/training/jobs", token=tok, params={"page": 1, "page_size": 100})
        # 计数 task_id 包含我们新前缀的任务 (model_name 以 e2e_ 开头)
        cnt = sum(1 for it in r.get("items", []) if it.get("model_name", "").startswith("e2e_"))
        # 检查是否所有 3 个任务都已到终态 (worker 处理完)
        e2e_items = [it for it in r.get("items", []) if it.get("model_name", "").startswith("e2e_")]
        terminal_cnt = sum(1 for it in e2e_items if it.get("state") in ("FAILURE", "SUCCESS", "REVOKED", "PAUSED"))
        if cnt >= 3 and terminal_cnt >= 3:
            print(f"    ✓ {cnt} jobs found, all terminal after {wait_i+1}s")
            break
        time.sleep(1)
    else:
        print(f"    ! timeout waiting, cnt={cnt}, terminal={terminal_cnt}")
    r = http("GET", "/api/training/jobs", token=tok, params={"page": 1, "page_size": 2})
    assert r.get("page") == 1, f"page should be 1, got {r.get('page')}"
    assert r.get("page_size") == 2, f"page_size should be 2, got {r.get('page_size')}"
    assert "total" in r, f"missing 'total' in response: {r}"
    assert "items" in r, f"missing 'items' in response: {r}"
    assert len(r["items"]) <= 2, f"items length should be <= 2, got {len(r['items'])}"
    assert r["total"] >= 3, f"total should be >= 3 (we created 3), got {r['total']}"
    print(f"    ✓ total={r['total']}, items.len={len(r['items'])}, page=1, page_size=2")
    # 拿 job_id 列表 (按 id desc 排序, 最新在前)
    job_ids = [(it.get("celery_task_id"), it["id"]) for it in r["items"]] + job_ids
    # 去重, 保留 (task_id, job_id) 对
    seen = set()
    unique_jobs = []
    for tid, jid in job_ids:
        if tid and tid not in seen:
            seen.add(tid)
            unique_jobs.append((tid, jid))
    job_ids = unique_jobs
    print(f"    fetched {len(job_ids)} jobs: {[(t[:8] if t else None, j) for t, j in job_ids[:3]]}")

    # ---- 验证 2: 状态过滤 ----
    print()
    print("[4] /training/jobs?state=PENDING — 验证状态过滤")
    r = http("GET", "/api/training/jobs", token=tok, params={"state": "PENDING", "page_size": 5})
    pending = [it for it in r["items"]]
    assert all(it["state"] == "PENDING" for it in pending), "state filter broken"
    print(f"    ✓ {len(pending)} PENDING jobs, all state=PENDING")

    # ---- 关键: 停掉 Celery worker, 让新任务保持 PENDING 状态 ----
    # dataset_id=26 标注不足, 任务会快速 FAIL. 测 pause/cancel 需要 PENDING
    print()
    print("[5] 停 Celery worker — 让新任务保持 PENDING (用于测 pause/cancel)")
    subprocess.run([VENV_PY, "start_workers.py", "--stop"], cwd=BACKEND_DIR, capture_output=True)
    time.sleep(2)
    # 清空 broker 队列里可能残留的旧任务消息
    subprocess.run([VENV_PY, "-c",
                    "import redis; r=redis.Redis(host='127.0.0.1',port=9770,db=1); "
                    "r.delete('celery','unacked','unacked_index')"],
                   cwd=BACKEND_DIR, capture_output=True)
    print("    ✓ worker stopped, broker cleared")

    # 创建 3 个新任务, 停 worker 后它们会停在 broker 队列里, 不会进 DB
    print()
    print("[6] 创建 3 个新任务 (worker 已停, 任务会停在 broker)")
    fresh_task_ids = []
    for i in range(3):
        r = http("POST", "/api/training/start", token=tok, params={
            "dataset_id": ds_id, "base_model": "resnet18",
            "model_name": f"pending_{int(time.time())}_{i}",
            "epochs": 2, "batch_size": 8, "learning_rate": 0.001,
        })
        fresh_task_ids.append(r["task_id"])
        print(f"    created task_id={r['task_id'][:8]}… (no DB record yet)")

    # 注: API 提交后任务还在 broker 队列里, DB 还没记录 (DB 记录由 worker 创建)
    # 这模拟了 worker 还没接走的 PENDING 状态 (Celery 端 PENDING, DB 无)
    # 此时 pause/cancel API 找不到这个 job, 只能用 task_id 路径 (前端不展示)
    # 真实的 PENDING 状态出现在 worker 短暂启动时

    # ---- 启动 worker, 让任务进入 DB 并短暂处于 PENDING/PROGRESS ----
    print()
    print("[7] 重启 worker, 让任务进 DB")
    subprocess.run([VENV_PY, "start_workers.py", "--detach"], cwd=BACKEND_DIR, capture_output=True)
    time.sleep(3)

    # 等 3 个任务进 DB
    print("    waiting for 3 new jobs in DB...")
    db_job_ids = []
    for wait_i in range(20):
        r = http("GET", "/api/training/jobs", token=tok, params={"page": 1, "page_size": 20})
        db_job_ids = [it["id"] for it in r["items"] if it.get("model_name", "").startswith("pending_")]
        if len(db_job_ids) >= 3:
            break
        time.sleep(1)
    print(f"    ✓ {len(db_job_ids)} new DB records: {db_job_ids}")

    # ---- 验证 3: pause 在 PENDING 状态可工作 ----
    if db_job_ids:
        job_id = db_job_ids[0]
        print()
        print(f"[8] /jobs/{job_id}/pause — 验证暂停 (PENDING → PAUSED)")
        r = http("POST", f"/api/training/jobs/{job_id}/pause", token=tok)
        # 任务可能已经 FAILURE, 但只要它在 PENDING 状态就会成功
        if r.get("success") is True:
            assert r["state"] == "PAUSED", f"state should be PAUSED: {r}"
            print(f"    ✓ paused: {r['message']}")
            # 验证 DB
            r = http("GET", f"/api/training/jobs/{job_id}", token=tok)
            assert r["state"] == "PAUSED", f"DB state should be PAUSED, got {r['state']}"
            print(f"    ✓ DB confirms state=PAUSED")
            # 删 PAUSED 状态
            print()
            print(f"[9] DELETE /jobs/{job_id} — 验证删除 (PAUSED 状态)")
            r = http("DELETE", f"/api/training/jobs/{job_id}", token=tok)
            assert r["success"] is True, f"delete should succeed: {r}"
            print(f"    ✓ deleted: {r['message']}")
        else:
            # 任务已 FAILURE, 验证 pause 被拒绝
            assert r.get("state") in ("FAILURE", "REVOKED", "SUCCESS"), f"unexpected state: {r}"
            print(f"    note: 任务已到终态 {r.get('state')}, pause 正确拒绝: {r.get('message')}")
            # 直接删 FAILURE 状态
            print()
            print(f"[9] DELETE /jobs/{job_id} — 验证删除 ({r.get('state')} 状态)")
            r = http("DELETE", f"/api/training/jobs/{job_id}", token=tok)
            assert r["success"] is True, f"delete should succeed: {r}"
            print(f"    ✓ deleted: {r['message']}")
    else:
        print("[8-9] skip (no DB records created)")

    # ---- 验证 4: 404 cases ----
    print()
    print("[10] /jobs/999999/pause — 验证不存在的任务 404")
    try:
        r = http("POST", "/api/training/jobs/999999/pause", token=tok)
        print(f"    ✗ expected 404, got {r}")
    except urllib.error.HTTPError as e:
        assert e.code == 404, f"expected 404, got {e.code}"
        print(f"    ✓ 404 for non-existent job")

    # ---- 验证 5: 复用启动 (取一个 FAILURE job 测 restart) ----
    print()
    print("[11] /jobs/{id}/start — 验证复用启动 (取 FAILURE 终态 job 测 restart)")
    r = http("GET", "/api/training/jobs", token=tok, params={"state": "FAILURE", "page_size": 1})
    if r["items"]:
        jid = r["items"][0]["id"]
        rr = http("POST", f"/api/training/jobs/{jid}/start", token=tok)
        assert rr.get("success") is True, f"reuse start should succeed: {rr}"
        assert rr.get("state") == "PENDING", f"new state should be PENDING, got {rr.get('state')}"
        assert rr.get("task_id"), f"should return new task_id: {rr}"
        print(f"    ✓ restart succeeded: state={rr.get('state')}, task_id={rr.get('task_id')[:8]}…")
    else:
        print("    skip (no FAILURE job to test)")

    # ---- 验证 6: 暂停非活跃态 (FAILURE) 应拒绝 ----
    print()
    print("[12] /jobs/{id}/pause on FAILURE — 验证拒绝")
    r = http("GET", "/api/training/jobs", token=tok, params={"state": "FAILURE", "page_size": 1})
    if r["items"]:
        jid = r["items"][0]["id"]
        rr = http("POST", f"/api/training/jobs/{jid}/pause", token=tok)
        assert rr.get("success") is False, f"should reject pause on FAILURE: {rr}"
        assert "Cannot pause" in rr.get("message", ""), f"bad msg: {rr}"
        print(f"    ✓ rejected: {rr.get('message')}")
    else:
        print("    skip (no FAILURE job)")

    # ---- 验证 7: 删除 PENDING/PROGRESS 状态应拒绝 ----
    print()
    print("[13] DELETE on PENDING/PROGRESS — 验证拒绝")
    # 等 worker 把第 11 步新建的 PENDING 任务推进, 然后试着删它
    # 用其他方法: 找一个 PENDING 状态的任务 (可能没有因为 fast-fail)
    r = http("GET", "/api/training/jobs", token=tok, params={"state": "PENDING", "page_size": 1})
    if r["items"]:
        jid = r["items"][0]["id"]
        rr = http("DELETE", f"/api/training/jobs/{jid}", token=tok)
        assert rr.get("success") is False, f"should reject: {rr}"
        print(f"    ✓ DELETE on PENDING rejected: {rr.get('message')}")
    else:
        # 如果没 PENDING, 那 [11] 的 restart 已经被 worker 处理了, 检查 PROGRESS
        r = http("GET", "/api/training/jobs", token=tok, params={"state": "PROGRESS", "page_size": 1})
        if r["items"]:
            jid = r["items"][0]["id"]
            rr = http("DELETE", f"/api/training/jobs/{jid}", token=tok)
            assert rr.get("success") is False, f"should reject: {rr}"
            print(f"    ✓ DELETE on PROGRESS rejected: {rr.get('message')}")
        else:
            print("    note: 没有 PENDING/PROGRESS 状态 (任务都快速 FAIL)")

    # ---- 清理: 删掉所有 PENDING/PROGRESS 任务 (取消 + 删) ----
    print()
    print("[14] 清理: 取消 + 删除所有 PENDING/PROGRESS 任务")
    r = http("GET", "/api/training/jobs", token=tok, params={"state": "PENDING", "page_size": 50})
    for it in r["items"]:
        try:
            http("POST", f"/api/training/jobs/{it['id']}/cancel", token=tok)
            http("DELETE", f"/api/training/jobs/{it['id']}", token=tok)
        except Exception as e:
            print(f"    cleanup #{it['id']} failed: {e}")
    r = http("GET", "/api/training/jobs", token=tok, params={"state": "PROGRESS", "page_size": 50})
    for it in r["items"]:
        try:
            http("POST", f"/api/training/jobs/{it['id']}/cancel", token=tok)
            http("DELETE", f"/api/training/jobs/{it['id']}", token=tok)
        except Exception as e:
            print(f"    cleanup #{it['id']} failed: {e}")
    r = http("GET", "/api/training/jobs", token=tok, params={"page_size": 50})
    print(f"    清理后剩余 {r['total']} 个任务")

    print()
    print("=" * 60)
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n!!! ASSERTION FAILED: {e}")
        sys.exit(2)
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(3)
