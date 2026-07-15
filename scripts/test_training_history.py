"""Directly probe /api/training/history with a fake task_id.
Was 500 (Redis TimeoutError) before Redis started.
"""
import json
import random
import string
import urllib.request

BASE = "http://127.0.0.1:5000"

# 1) 注册
username = "thesis_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
password = "Test1234!"
req = urllib.request.Request(
    f"{BASE}/api/auth/register",
    data=json.dumps({"username": username, "password": password, "email": f"{username}@x.com"}).encode(),
    headers={"Content-Type": "application/json"},
)
d = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
tok = d["access_token"]
HDR = {"Authorization": f"Bearer {tok}"}
print(f"[1] register user={username}, id={d['user_id']}")

# 2) 测 history (fake task_id, 不存在 → 应走 celery AsyncResult 查询, Redis 不可达时曾 500)
fake_id = "00000000-0000-0000-0000-000000000000"
req = urllib.request.Request(f"{BASE}/api/training/history/{fake_id}", headers=HDR)
try:
    r = urllib.request.urlopen(req, timeout=8)
    print(f"[2] GET /api/training/history/{fake_id[:8]} -> {r.status}")
    print("    body:", r.read().decode()[:200])
except urllib.error.HTTPError as e:
    print(f"[2] GET /api/training/history/{fake_id[:8]} -> {e.code}")
    print("    body:", e.read().decode()[:400])
except Exception as e:
    print(f"[2] GET /api/training/history/{fake_id[:8]} -> EXC {type(e).__name__}: {str(e)[:200]}")

# 3) 测 progress
req = urllib.request.Request(f"{BASE}/api/training/progress/{fake_id}", headers=HDR)
try:
    r = urllib.request.urlopen(req, timeout=8)
    print(f"[3] GET /api/training/progress/{fake_id[:8]} -> {r.status}")
    print("    body:", r.read().decode()[:200])
except urllib.error.HTTPError as e:
    print(f"[3] GET /api/training/progress/{fake_id[:8]} -> {e.code}")
    print("    body:", e.read().decode()[:400])
except Exception as e:
    print(f"[3] GET /api/training/progress/{fake_id[:8]} -> EXC {type(e).__name__}: {str(e)[:200]}")
