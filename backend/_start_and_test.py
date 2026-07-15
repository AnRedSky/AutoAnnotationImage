"""All-in-one: start backend, test all endpoints, leave running."""
import os
import sys
import time
import subprocess
import json
import urllib.request
import urllib.error
import urllib.parse

ROOT = r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation"
BACKEND = os.path.join(ROOT, "backend")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)

PY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")

# === 1. Start backend (detached) ===
log_out = open(os.path.join(LOGS, "backend_e2e.log"), "w")
log_err = open(os.path.join(LOGS, "backend_e2e.err.log"), "w")

p = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app",
     "--host", "127.0.0.1", "--port", "5000"],
    cwd=BACKEND,
    stdout=log_out,
    stderr=log_err,
    stdin=subprocess.DEVNULL,
    creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    close_fds=True,
)
print(f"[OK] Backend started, PID={p.pid}")

# === 2. Wait for health ===
ok = False
for i in range(45):
    try:
        r = urllib.request.urlopen("http://127.0.0.1:5000/api/health", timeout=2)
        if r.status == 200:
            print(f"[OK] Backend health check (after {i+1}s)")
            ok = True
            break
    except Exception:
        pass
    time.sleep(1)

if not ok:
    log_err.flush()
    print("[FAIL] Backend failed to start. Logs:")
    print("--- STDOUT ---")
    print(open(log_out.name).read()[:3000])
    print("--- STDERR ---")
    print(open(log_err.name).read()[:3000])
    sys.exit(1)

# === 3. Test endpoints ===
def http(method, path, data=None, token=None, expect_status=None):
    url = f"http://127.0.0.1:5000{path}"
    headers = {}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        status, text = r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", errors="replace")
    return status, text


def post_form(path, form_data):
    body = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:5000{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def check(name, status, body, expect=200, must_have=None):
    ok_status = (status == expect) if isinstance(expect, int) else (status in expect)
    ok_content = True
    if must_have:
        ok_content = all(s in body for s in must_have)
    flag = "PASS" if (ok_status and ok_content) else "FAIL"
    print(f"  [{flag}] {name}: HTTP {status}")
    if not ok_status or not ok_content:
        print(f"         body: {body[:400]}")
    return ok_status and ok_content


results = []
print("\n=== Functional Tests ===\n")

# 1. Health
s, b = http("GET", "/api/health")
results.append(check("GET /api/health", s, b, 200, ["status"]))

# 2. System info
s, b = http("GET", "/api/system/info")
results.append(check("GET /api/system/info", s, b, 200, ["python", "torch"]))

# 3. Root
s, b = http("GET", "/")
results.append(check("GET /", s, b, 200, ["Image Annotation"]))

# 4. Register
uname = f"e2e_{int(time.time())}"
s, b = http("POST", "/api/auth/register",
            {"username": uname, "password": "Test123456", "email": f"{uname}@x.com", "role": "admin"})
results.append(check(f"POST register {uname}", s, b, 200, ["id"]))

# 5. Login
s, b = post_form("/api/auth/login", {"username": uname, "password": "Test123456"})
results.append(check("POST login", s, b, 200, ["access_token"]))
try:
    token = json.loads(b)["access_token"]
except Exception:
    token = None

# 6. Get current user
if token:
    s, b = http("GET", "/api/auth/me", token=token)
    results.append(check("GET /api/auth/me", s, b, 200, ["username"]))

# 7. Create dataset
if token:
    s, b = http("POST", "/api/datasets",
                {"name": f"ds_{int(time.time())}", "task_type": "classification",
                 "category_names": ["cat_a", "cat_b", "cat_c"]},
                token=token)
    results.append(check("POST /api/datasets", s, b, 200, ["id"]))
    try:
        ds = json.loads(b)
        ds_id = ds.get("id")
    except Exception:
        ds_id = None

# 8. List datasets
if token:
    s, b = http("GET", "/api/datasets", token=token)
    results.append(check("GET /api/datasets", s, b, 200, ["items"]))

# 9. List categories
if token and ds_id:
    s, b = http("GET", f"/api/datasets/{ds_id}/categories", token=token)
    results.append(check(f"GET /api/datasets/{ds_id}/categories", s, b, 200))

# 10. List images
if token and ds_id:
    s, b = http("GET", f"/api/images/list/{ds_id}?page=1&page_size=20", token=token)
    results.append(check(f"GET /api/images/list/{ds_id}", s, b, 200))

# 11. Stats overview
if token:
    s, b = http("GET", "/api/stats/overview", token=token)
    results.append(check("GET /api/stats/overview", s, b, 200))

# 12. List models
if token:
    s, b = http("GET", "/api/models/", token=token)
    results.append(check("GET /api/models/", s, b, 200))

# 13. Auto-annotate models
if token:
    s, b = http("GET", "/api/auto-annotate/models", token=token)
    results.append(check("GET /api/auto-annotate/models", s, b, 200))

# 14. Training progress (fake task id)
if token:
    s, b = http("GET", "/api/training/progress/00000000-0000-0000-0000-000000000000", token=token)
    # may be 200 with PENDING or 404 - both acceptable
    ok_t = s in (200, 404)
    results.append(ok_t)

# 15. Export COCO (just check that endpoint exists, may return 200 with empty data)
if token and ds_id:
    s, b = http("GET", f"/api/export/coco/{ds_id}", token=token)
    print(f"  [INFO] GET /api/export/coco/{ds_id}: HTTP {s}, body len={len(b)}")
    results.append(s in (200, 400, 404))

# 16. Export YOLO
if token and ds_id:
    s, b = http("GET", f"/api/export/yolo/{ds_id}", token=token)
    print(f"  [INFO] GET /api/export/yolo/{ds_id}: HTTP {s}, body len={len(b)}")
    results.append(s in (200, 400, 404))

# === Summary ===
passed = sum(1 for r in results if r)
failed = len(results) - passed
print(f"\n=== Results: {passed} PASS / {failed} FAIL (total {len(results)}) ===")

# Save PID so user can stop it later
with open(os.path.join(LOGS, "backend.pid"), "w") as f:
    f.write(str(p.pid))

print(f"\nBackend PID={p.pid} is still running on http://127.0.0.1:5000")
print(f"To stop: kill -9 {p.pid}  (or run scripts\\stop_local.ps1)")
