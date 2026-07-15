"""All-in-one: start backend (detached), wait, run e2e tests, leave running."""
import os
import sys
import time
import struct
import zlib
import io
import json
import subprocess
import urllib.request
import urllib.error
import urllib.parse

BASE = "http://127.0.0.1:5000"
ROOT = r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation"
BACKEND = os.path.join(ROOT, "backend")
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
PY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")


def http(method, path, data=None, token=None, raw_data=None, content_type="application/json"):
    url = f"{BASE}{path}"
    headers = {}
    body = None
    if raw_data is not None:
        body = raw_data
        headers["Content-Type"] = content_type
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def post_form(path, form_data):
    body = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(f"{BASE}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def make_png(width=64, height=64, color=(100, 150, 200)):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    for y in range(height):
        raw += b"\x00"
        for x in range(width):
            raw += bytes(color)
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_jpeg(width=64, height=64):
    try:
        from PIL import Image
        img = Image.new("RGB", (width, height), (200, 100, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return b""


def check(name, status, body, expect=200, must_have=None):
    if isinstance(expect, int):
        ok = (status == expect)
    else:
        ok = (status in expect)
    if must_have:
        ok = ok and all(s in body for s in must_have)
    flag = "PASS" if ok else "FAIL"
    print(f"  [{flag}] {name}: HTTP {status}")
    if not ok:
        print(f"         body: {body[:400]}")
    return ok


def start_backend():
    log_out = open(os.path.join(LOGS, "backend_e2e.log"), "w")
    log_err = open(os.path.join(LOGS, "backend_e2e.err.log"), "w")
    p = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "5000"],
        cwd=BACKEND,
        stdout=log_out, stderr=log_err, stdin=subprocess.DEVNULL,
        creationflags=0x8 | 0x200,
        close_fds=True,
    )
    print(f"[OK] Backend started, PID={p.pid}")
    return p


def wait_health(timeout=45):
    for i in range(timeout):
        try:
            r = urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            if r.status == 200:
                print(f"[OK] Backend health check (after {i+1}s)")
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    p = start_backend()
    if not wait_health():
        print("[FAIL] Backend not ready. See backend_e2e.err.log")
        return 1

    results = []
    print("=" * 60)
    print("  Full E2E Integration Test")
    print("=" * 60)

    # 1. Health & System
    print("\n[1] Health & System")
    s, b = http("GET", "/api/health")
    results.append(check("GET /api/health", s, b, 200, ["status"]))
    s, b = http("GET", "/api/system/info")
    results.append(check("GET /api/system/info", s, b, 200, ["python", "torch"]))

    # 2. Auth
    print("\n[2] Auth")
    uname = f"e2e_full_{int(time.time())}"
    s, b = http("POST", "/api/auth/register",
                {"username": uname, "password": "Test123456", "email": f"{uname}@x.com", "role": "admin"})
    results.append(check(f"Register {uname}", s, b, 200, ["id"]))
    s, b = post_form("/api/auth/login", {"username": uname, "password": "Test123456"})
    results.append(check("Login", s, b, 200, ["access_token"]))
    token = json.loads(b).get("access_token") if s == 200 else None
    if not token:
        print("[FAIL] No token, aborting")
        return 1
    s, b = http("GET", "/api/auth/me", token=token)
    results.append(check("GET /api/auth/me", s, b, 200, [uname]))

    # 3. Dataset CRUD
    print("\n[3] Dataset CRUD")
    s, b = http("POST", "/api/datasets",
                {"name": f"full_e2e_{int(time.time())}", "task_type": "classification",
                 "category_names": ["dog", "cat", "bird"]},
                token=token)
    results.append(check("Create dataset", s, b, 200, ["id"]))
    try:
        ds = json.loads(b); ds_id = ds.get("id")
    except Exception:
        ds_id = None
    print(f"  [INFO] dataset_id={ds_id}")
    s, b = http("GET", "/api/datasets", token=token)
    results.append(check("List datasets", s, b, 200, ["items"]))
    s, b = http("GET", f"/api/datasets/{ds_id}", token=token)
    results.append(check(f"Get dataset {ds_id}", s, b, 200, ["categories"]))
    s, b = http("GET", f"/api/datasets/{ds_id}/categories", token=token)
    results.append(check(f"List categories", s, b, 200))
    cat_ids = []
    try:
        cat_data = json.loads(b)
        if isinstance(cat_data, dict) and "items" in cat_data:
            cat_ids = [c["id"] for c in cat_data["items"]]
        elif isinstance(cat_data, list):
            cat_ids = [c["id"] for c in cat_data]
    except Exception:
        pass
    print(f"  [INFO] category ids: {cat_ids}")

    # 4. Image Upload
    print("\n[4] Image Upload")
    png_bytes = make_png(64, 64, (255, 100, 50))
    jpg_bytes = make_jpeg(64, 64) or make_png(64, 64, (200, 100, 50))
    boundary = "----E2EBoundary" + str(int(time.time()))
    for fname, data, ctype in [
        ("test1.png", png_bytes, "image/png"),
        ("test2.jpg", jpg_bytes, "image/jpeg"),
        ("test3.png", make_png(64, 64, (50, 200, 100)), "image/png"),
    ]:
        body = (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files"; filename="{fname}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        s, b = http("POST", f"/api/images/upload/{ds_id}", token=token, raw_data=body,
                    content_type=f"multipart/form-data; boundary={boundary}")
        results.append(check(f"Upload {fname}", s, b, 200, ["items"]))
    s, b = http("GET", f"/api/images/list/{ds_id}?page=1&page_size=20", token=token)
    results.append(check("List images", s, b, 200, ["items"]))
    img_id = None
    try:
        items = json.loads(b).get("items", [])
        if items: img_id = items[0]["id"]
    except Exception:
        pass
    print(f"  [INFO] first image id={img_id}")

    # 5. Annotation
    print("\n[5] Annotation")
    if img_id and cat_ids:
        s, b = http("POST", "/api/annotations/save",
                    {"image_id": img_id, "label_id": cat_ids[0],
                     "time_spent_ms": 1500, "is_confirm": True},
                    token=token)
        results.append(check("Save annotation (confirm)", s, b, 200, ["success"]))
        s, b = http("GET", f"/api/annotations/stats/{ds_id}", token=token)
        results.append(check("Annotation stats", s, b, 200))

    # 6. Stats (the 2 endpoints we fixed)
    print("\n[6] Statistics")
    for ep in ["/api/stats/overview",
               f"/api/stats/dataset/{ds_id}",
               f"/api/stats/confidence/{ds_id}",
               f"/api/stats/timeline/{ds_id}?days=7",
               "/api/stats/annotator-efficiency"]:
        s, b = http("GET", ep, token=token)
        results.append(check(f"GET {ep}", s, b, 200))

    # 7. Models & Training
    print("\n[7] Model & Training")
    s, b = http("GET", "/api/models/", token=token)
    results.append(check("List models", s, b, 200))
    s, b = http("GET", "/api/training/jobs", token=token)
    results.append(check("List training jobs", s, b, 200))
    s, b = http("GET", "/api/auto-annotate/models", token=token)
    results.append(check("List auto-annotate models", s, b, 200))

    # 8. Export
    print("\n[8] Export")
    s, b = http("GET", f"/api/export/coco/{ds_id}", token=token)
    results.append(check("Export COCO", s, b, 200))
    s, b = http("GET", f"/api/export/yolo/{ds_id}", token=token)
    results.append(check("Export YOLO", s, b, 200))
    s, b = http("GET", f"/api/export/csv/{ds_id}", token=token)
    results.append(check("Export CSV", s, b, 200))

    # === Summary ===
    passed = sum(1 for r in results if r)
    failed = len(results) - passed
    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} PASS / {failed} FAIL (total {len(results)})")
    print(f"{'=' * 60}")

    with open(os.path.join(LOGS, "backend.pid"), "w") as f:
        f.write(str(p.pid))
    print(f"\nBackend PID={p.pid} still running on http://127.0.0.1:5000")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
