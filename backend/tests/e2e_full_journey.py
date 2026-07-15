"""
Full user-journey E2E: list images -> preview image -> activate model -> predict.
No mocks, real HTTP calls. Reports PASS/FAIL per step.
"""
import sys, time, base64, json
import httpx

BASE = "http://127.0.0.1:5000"

def log(t, m): print(f"[{t}] {m}", flush=True)
def fail(m): log("FAIL", m); sys.exit(1)
def ok(m): log("OK", m)

def main():
    log("START", "=== Full user-journey E2E ===")

    # 1) Login
    r = httpx.post(f"{BASE}/api/auth/login", data={"username":"admin","password":"admin123"})
    if r.status_code != 200: fail(f"login {r.status_code} {r.text[:200]}")
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    ok("login")

    # 2) List images in dataset 20
    r = httpx.get(f"{BASE}/api/images/list/20?limit=5", headers=H)
    if r.status_code != 200: fail(f"images {r.status_code} {r.text[:200]}")
    images = r.json().get("items", r.json())
    if not isinstance(images, list) or not images:
        fail(f"no images: {r.text[:200]}")
    img_id = images[0]["id"]
    ok(f"images list, sample id={img_id}")

    # 3) Preview image WITHOUT auth (should work if files.py supports optional auth)
    r = httpx.get(f"{BASE}/api/files/{img_id}", timeout=10)
    if r.status_code == 200:
        size = len(r.content)
        ok(f"image preview WITHOUT auth, {size} bytes, content-type={r.headers.get('content-type')}")
    else:
        log("WARN", f"image preview without auth: {r.status_code} {r.text[:200]}")

    # 4) Preview image WITH auth
    r = httpx.get(f"{BASE}/api/files/{img_id}?token={token}", timeout=10)
    if r.status_code == 200:
        ok(f"image preview WITH token, {len(r.content)} bytes")
    else:
        fail(f"image preview with token: {r.status_code} {r.text[:200]}")

    # 5) List models
    r = httpx.get(f"{BASE}/api/models/", headers=H)
    if r.status_code != 200: fail(f"models {r.status_code} {r.text[:200]}")
    models = r.json().get("items", r.json())
    log("INFO", f"models count={len(models) if isinstance(models, list) else 'N/A'}")
    if isinstance(models, list) and models:
        for m in models[:5]:
            log("MODEL", f"  id={m.get('id')} name={m.get('name')} active={m.get('is_active')} acc={m.get('accuracy')}")

    # 6) Activate most recent v1 model
    v1_models = [m for m in models if m.get("name") == "v1"] if isinstance(models, list) else []
    if not v1_models:
        log("WARN", "no v1 model to activate")
    else:
        target = sorted(v1_models, key=lambda x: x.get("id", 0), reverse=True)[0]
        r = httpx.post(f"{BASE}/api/models/{target['id']}/activate", headers=H)
        log("ACT", f"activate model {target['id']}: {r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            ok(f"model {target['id']} activated")
        else:
            log("WARN", f"activate failed: {r.text[:200]}")

    # 7) Auto-annotate to load model and run prediction
    if isinstance(models, list) and v1_models:
        r = httpx.post(f"{BASE}/api/auto-annotate/run",
                       json={"dataset_id": 20, "model_name": "v1",
                             "confidence_threshold": 0.5, "async_mode": True},
                       headers=H, timeout=30)
        log("PRED", f"auto-annotate: {r.status_code} {r.text[:300]}")
        if r.status_code in (200, 202):
            data = r.json()
            ok(f"auto-annotate done, total={data.get('total')} auto_labeled={data.get('auto_labeled')}")
        else:
            log("WARN", f"auto-annotate failed: {r.text[:200]}")

    log("DONE", "=== E2E finished ===")

if __name__ == "__main__":
    main()
