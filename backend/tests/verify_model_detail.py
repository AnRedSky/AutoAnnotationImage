"""Verify model detail endpoint now returns is_active (and other previously missing fields)."""
import json
import urllib.request
import urllib.parse

body = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
r = urllib.request.Request("http://127.0.0.1:5000/api/auth/login", data=body,
                           headers={"Content-Type": "application/x-www-form-urlencoded"},
                           method="POST")
with urllib.request.urlopen(r) as resp:
    token = json.loads(resp.read())["access_token"]

# 1) List models, pick first
r = urllib.request.Request("http://127.0.0.1:5000/api/models", headers={"Authorization": "Bearer " + token})
with urllib.request.urlopen(r) as resp:
    d = json.loads(resp.read())
items = d.get("items", d if isinstance(d, list) else [])
print(f"[1] list models count={len(items)}")
if not items:
    print("    no models, abort")
    raise SystemExit(1)
m = items[0]
print(f"    first model: id={m['id']} name={m['name']!r} is_active={m.get('is_active')}")

# 2) Fetch detail of first model
mid = m["id"]
r = urllib.request.Request(f"http://127.0.0.1:5000/api/models/{mid}/detail", headers={"Authorization": "Bearer " + token})
with urllib.request.urlopen(r) as resp:
    detail = json.loads(resp.read())
print(f"[2] detail of model {mid}:")
for k, v in detail.items():
    preview = json.dumps(v, ensure_ascii=False)
    if len(preview) > 80:
        preview = preview[:80] + "..."
    print(f"    {k}: {preview}")

# 3) Check critical fields
required = ["id", "name", "base_model", "dataset_id", "num_classes",
            "accuracy", "precision", "recall", "f1_score", "is_active",
            "created_at", "file_path"]
missing = [k for k in required if k not in detail]
if missing:
    print(f"[3-FAIL] missing fields: {missing}")
    raise SystemExit(1)
print(f"[3-PASS] all {len(required)} required fields present")

# 4) Compare with list entry
print(f"[4] consistency check:")
print(f"    list.is_active  = {m.get('is_active')}")
print(f"    detail.is_active = {detail.get('is_active')}")
print(f"    list.accuracy    = {m.get('accuracy')}")
print(f"    detail.accuracy  = {detail.get('accuracy')}")
if m.get("is_active") != detail.get("is_active"):
    print("[4-FAIL] is_active differs between list and detail")
    raise SystemExit(1)
print("[4-PASS] list and detail show consistent state")
