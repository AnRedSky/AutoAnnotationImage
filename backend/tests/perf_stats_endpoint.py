"""
Performance Test: Stats Endpoint Latency
=======================================
6 个统计接口响应时延
"""
import asyncio
import time
import statistics
from pathlib import Path

import httpx


API_BASE = "http://127.0.0.1:8000"
USERNAME = "admin"
PASSWORD = "admin123"
DATASET_ID = 1
N_PER_ENDPOINT = 5

ENDPOINTS = [
    ("overview", "GET", "/api/stats/overview", None),
    ("dataset", "GET", f"/api/stats/dataset/{DATASET_ID}", None),
    ("confidence", "GET", f"/api/stats/confidence/{DATASET_ID}", None),
    ("timeline", "GET", f"/api/stats/timeline/{DATASET_ID}", None),
    ("annotator-efficiency", "GET", "/api/stats/annotator-efficiency", None),
    ("models-compare", "GET", "/api/stats/models/compare/1/2", None),  # 假设 2 个模型存在
]


async def main():
    async with httpx.AsyncClient() as client:
        login = await client.post(
            f"{API_BASE}/api/auth/login",
            data={"username": USERNAME, "password": PASSWORD},
        )
        if login.status_code != 200:
            print(f"[fail] login failed: {login.status_code}")
            return
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        out_lines = ["endpoint,avg_ms,p50_ms,p95_ms,max_ms,status"]

        for name, method, path, body in ENDPOINTS:
            durations = []
            ok = True
            for _ in range(N_PER_ENDPOINT):
                t0 = time.perf_counter()
                try:
                    if method == "GET":
                        r = await client.get(f"{API_BASE}{path}", headers=headers, timeout=10.0)
                    else:
                        r = await client.post(f"{API_BASE}{path}", headers=headers, json=body, timeout=10.0)
                    if r.status_code >= 500:
                        ok = False
                except Exception:
                    ok = False
                durations.append((time.perf_counter() - t0) * 1000)
            avg = statistics.mean(durations)
            p50 = statistics.median(durations)
            p95 = sorted(durations)[int(len(durations) * 0.95)]
            mx = max(durations)
            print(f"  {name:30s} avg={avg:6.0f}ms  p95={p95:6.0f}ms  {'OK' if ok else 'FAIL'}")
            out_lines.append(f"{name},{avg:.0f},{p50:.0f},{p95:.0f},{mx:.0f},{'OK' if ok else 'FAIL'}")

        results_dir = Path("tests/results")
        results_dir.mkdir(parents=True, exist_ok=True)
        out_path = results_dir / "perf_stats_endpoint.csv"
        out_path.write_text("\n".join(out_lines), encoding="utf-8")
        print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
