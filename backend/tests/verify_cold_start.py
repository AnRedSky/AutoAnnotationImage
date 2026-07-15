"""End-to-end smoke test for the cold-start fixes:
1. AI 预标注 fallback to ImageNet when no active fine-tune model
2. Training threshold lowered from 10 to 2
"""
import json
import time
import urllib.request
import urllib.error
import urllib.parse

BASE = "http://127.0.0.1:5000"


def req(method, path, token=None, body=None, form=False):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"raw": str(e)}


def main():
    # 1. login
    code, d = req("POST", "/api/auth/login", body={"username": "admin", "password": "admin123"}, form=True)
    assert code == 200, d
    token = d["access_token"]
    print(f"[1] login ok, token len={len(token)}")

    # 2. list datasets, pick first one
    code, d = req("GET", "/api/datasets", token=token)
    items_ds = d.get("items") if isinstance(d, dict) else d
    print(f"[2] datasets: code={code}, count={len(items_ds) if isinstance(items_ds, list) else 'N/A'}")
    if isinstance(items_ds, list) and items_ds:
        ds = items_ds[0]
        print(f"    first dataset: id={ds.get('id')}, name={ds.get('name')}, image_count={ds.get('image_count', '?')}, annotated_count={ds.get('annotated_count', '?')}, category_count={ds.get('category_count', '?')}")
        ds_id = ds["id"]
    else:
        print("    NO datasets, abort")
        return

    # 3. check categories
    code, d = req("GET", f"/api/datasets/{ds_id}/categories", token=token)
    print(f"[3] categories: code={code}, count={len(d) if isinstance(d, list) else 'N/A'}")
    if isinstance(d, list):
        for c in d[:5]:
            print(f"    - {c.get('id')}: {c.get('name')}")

    # 4. check image stats
    code, d = req("GET", f"/api/datasets/{ds_id}/images?page=1&page_size=5", token=token)
    items = d.get("items") if isinstance(d, dict) else d
    print(f"[4] images: code={code}, sample_count={len(items) if items else 0}")
    if items:
        statuses = {}
        for it in items:
            s = it.get("status", "?")
            statuses[s] = statuses.get(s, 0) + 1
        print(f"    sample statuses: {statuses}")

    # 5. Test auto-annotate fallback (the bug from 问题1)
    print("\n[5] testing AI 预标注 fallback (no fine-tune model scenario)...")
    # Endpoint: POST /api/images/auto-label/{dataset_id}?use_finetune=true&...
    code, d = req("POST", f"/api/images/auto-label/{ds_id}?use_finetune=true&confidence_threshold=0.05&limit=3", token=token)
    print(f"    auto-label code={code}")
    print(f"    auto-label resp: {json.dumps(d, ensure_ascii=False)[:500]}")
    if code == 200:
        used = d.get("used_finetune", "?")
        fallback = d.get("fallback_to_pretrained", "?")
        warning = d.get("warning", "")
        labeled = d.get("auto_labeled", "?")
        print(f"    used_finetune={used}, fallback={fallback}, auto_labeled={labeled}")
        print(f"    warning present: {bool(warning)}")
        if warning:
            print(f"    warning: {warning[:200]}")
    elif code == 400:
        print(f"    got 400 (likely no images match): {d}")

    # 6. Test training with new threshold=2
    print("\n[6] testing training with threshold=2...")
    bm = "resnet18"  # default base model
    qs = f"dataset_id={ds_id}&base_model={bm}&model_name=e2e_test_v1&epochs=1&batch_size=4&learning_rate=0.001"
    code, d = req("POST", f"/api/training/start?{qs}", token=token)
    print(f"    start training: code={code}, resp={json.dumps(d, ensure_ascii=False)[:300]}")

    if code == 200:
        task_id = d.get("task_id")
        print(f"    task_id={task_id}")
        # Poll progress (note: response field is 'state' not 'status')
        for i in range(60):
            time.sleep(2)
            c, dd = req("GET", f"/api/training/progress/{task_id}", token=token)
            state = dd.get("state", "?")
            prog = dd.get("progress", "?")
            msg = dd.get("message", "")
            epoch = dd.get("current_epoch", "/")
            total = dd.get("total_epochs", "/")
            print(f"    poll {i:2d}: state={state:8s} progress={prog:6} epoch={epoch}/{total} {('msg=' + msg[:60]) if msg else ''}")
            if state in ("SUCCESS", "FAILURE", "REVOKED"):
                break
        print(f"\n[6-final] final state={state} progress={prog}")
        assert state == "SUCCESS", f"Expected SUCCESS, got {state}: {msg}"
        assert float(prog) == 100.0, f"Expected 100.0 progress, got {prog}"
        print("[6-PASS] training threshold=2 + DB fallback working")
    elif code == 400:
        print(f"    400 error: {d}")


if __name__ == "__main__":
    main()
