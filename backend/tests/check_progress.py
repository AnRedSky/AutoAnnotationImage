"""Quick check of progress endpoint behavior for known-stable jobs."""
import json
import time
import urllib.request
import urllib.parse

body = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
r = urllib.request.Request("http://127.0.0.1:5000/api/auth/login", data=body,
                           headers={"Content-Type": "application/x-www-form-urlencoded"},
                           method="POST")
with urllib.request.urlopen(r) as resp:
    token = json.loads(resp.read())["access_token"]

# 已知稳定的 job 78 (d306e9cb)
tid = "d306e9cb-8554-4349-8e0c-cd61900ad16f"
for i in range(5):
    r = urllib.request.Request(f"http://127.0.0.1:5000/api/training/progress/{tid}",
                                headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(r) as resp:
        d = json.loads(resp.read())
    print(f"  attempt {i}: state={d['state']:8s} progress={d['progress']:6} epoch={d['current_epoch']}/{d['total_epochs']} msg={d['message']!r}")
    time.sleep(1)
