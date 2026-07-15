"""
Login / register smoke test against running backend on :5000
Usage:  python scripts/test_login.py
"""
import json
import random
import string
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:5000"


def call(method, path, data=None, headers=None, json_body=False, timeout=10):
    url = f"{BASE}{path}"
    if data is not None and json_body:
        body = json.dumps(data).encode()
        headers = {**(headers or {}), "Content-Type": "application/json"}
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers = {**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"}
    else:
        body = None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return -1, str(e)


def main():
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    username = f"thesis_{rand}"
    password = "Test1234!"
    email = f"{username}@example.com"

    print("=" * 60)
    print(f"  Login smoke test  ->  {BASE}")
    print("=" * 60)

    # 1) /api/health
    code, body = call("GET", "/api/health")
    print(f"\n[1] GET /api/health          -> {code}")
    try:
        print("    " + json.dumps(json.loads(body), ensure_ascii=False, indent=2)[:400].replace("\n", "\n    "))
    except Exception:
        print("    " + body[:200])

    # 2) POST /api/auth/register   (JSON body — pydantic RegisterRequest)
    code, body = call("POST", "/api/auth/register",
                      {"username": username, "password": password, "email": email},
                      json_body=True)
    print(f"\n[2] POST /api/auth/register  -> {code}")
    print("    " + body[:300])
    if code != 200:
        print("    [FATAL] register failed; cannot proceed with login test")
        sys.exit(1)

    # 3) POST /api/auth/login (OAuth2PasswordRequestForm)
    code, body = call("POST", "/api/auth/login",
                      {"username": username, "password": password})
    print(f"\n[3] POST /api/auth/login     -> {code}")
    print("    " + body[:400])
    token = None
    try:
        token = json.loads(body).get("access_token")
    except Exception:
        pass
    if code != 200 or not token:
        print("    [FATAL] login failed")
        sys.exit(1)

    # 4) GET /api/auth/me  (auth check)
    code, body = call("GET", "/api/auth/me",
                      headers={"Authorization": f"Bearer {token}"})
    print(f"\n[4] GET /api/auth/me         -> {code}")
    print("    " + body[:300])

    # 5) Negative: wrong password
    code, body = call("POST", "/api/auth/login",
                      {"username": username, "password": "WrongPass!"})
    print(f"\n[5] POST /api/auth/login BAD -> {code}  (expected 401)")
    print("    " + body[:200])

    # 6) Negative: bad token
    code, body = call("GET", "/api/auth/me",
                      headers={"Authorization": "Bearer invalid.token.here"})
    print(f"\n[6] GET /api/auth/me BAD    -> {code}  (expected 401)")
    print("    " + body[:200])

    # 7) POST /api/auth/logout
    code, body = call("POST", "/api/auth/logout",
                      headers={"Authorization": f"Bearer {token}"})
    print(f"\n[7] POST /api/auth/logout    -> {code}")
    print("    " + body[:200])

    print("\n" + "=" * 60)
    print("  All login-related probes done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
