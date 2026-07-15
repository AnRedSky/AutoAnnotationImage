"""Quick health probe."""
import json
import urllib.request

r = urllib.request.urlopen("http://127.0.0.1:5000/api/health", timeout=5)
d = json.loads(r.read().decode())
print("GET /api/health ->", r.status)
for k, v in d["checks"].items():
    info = v.get("info", v.get("error", ""))
    ok = v.get("ok")
    print("  {:10s}: ok={}  info={}".format(k, ok, str(info)[:80]))
