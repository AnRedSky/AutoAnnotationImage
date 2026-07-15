"""
Performance Test: Concurrent Image Upload
=========================================
50 张图片并发上传, 统计总耗时 / 平均耗时 / 成功率
"""
import asyncio
import io
import time
import statistics
from pathlib import Path

import httpx
from PIL import Image


API_BASE = "http://127.0.0.1:8000"
USERNAME = "admin"
PASSWORD = "admin123"
DATASET_ID = 1
N = 50


def make_png_bytes(seed: int) -> bytes:
    img = Image.new("RGB", (64, 64), color=(seed % 255, (seed * 3) % 255, (seed * 7) % 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def upload_one(client: httpx.AsyncClient, token: str, idx: int) -> float:
    headers = {"Authorization": f"Bearer {token}"}
    files = [("files", (f"perf_{idx:04d}.png", make_png_bytes(idx), "image/png"))]
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{API_BASE}/api/images/upload/{DATASET_ID}",
            headers=headers,
            files=files,
            timeout=30.0,
        )
        ok = resp.status_code in (200, 201)
    except Exception:
        ok = False
    return time.perf_counter() - t0, ok


async def main():
    async with httpx.AsyncClient() as client:
        # 1) 登录
        login = await client.post(
            f"{API_BASE}/api/auth/login",
            data={"username": USERNAME, "password": PASSWORD},
        )
        if login.status_code != 200:
            print(f"[fail] login failed: {login.status_code} {login.text}")
            return
        token = login.json()["access_token"]
        print(f"[ok] logged in, token len = {len(token)}")

        # 2) 并发上传
        print(f"[start] uploading {N} images concurrently...")
        t0 = time.perf_counter()
        results = await asyncio.gather(*[upload_one(client, token, i) for i in range(N)])
        total = time.perf_counter() - t0

        durations = [d for d, ok in results]
        successes = [ok for d, ok in results]
        success_rate = sum(successes) / len(successes)

        print()
        print("=" * 50)
        print("Concurrent Upload Performance")
        print("=" * 50)
        print(f"Total:       {total:.2f}s")
        print(f"Throughput:  {N / total:.1f} req/s")
        print(f"Avg:         {statistics.mean(durations) * 1000:.0f} ms")
        print(f"P50:         {statistics.median(durations) * 1000:.0f} ms")
        print(f"P95:         {sorted(durations)[int(len(durations) * 0.95)] * 1000:.0f} ms")
        print(f"Max:         {max(durations) * 1000:.0f} ms")
        print(f"Success:     {sum(successes)}/{N} ({success_rate * 100:.0f}%)")

        # 3) 写结果到 tests/results/
        results_dir = Path("tests/results")
        results_dir.mkdir(parents=True, exist_ok=True)
        with open(results_dir / "perf_concurrent_upload.txt", "w", encoding="utf-8") as f:
            f.write(f"N={N}\n")
            f.write(f"Total seconds: {total:.2f}\n")
            f.write(f"Throughput: {N / total:.1f} req/s\n")
            f.write(f"Avg ms: {statistics.mean(durations) * 1000:.0f}\n")
            f.write(f"P50 ms: {statistics.median(durations) * 1000:.0f}\n")
            f.write(f"P95 ms: {sorted(durations)[int(len(durations) * 0.95)] * 1000:.0f}\n")
            f.write(f"Max ms: {max(durations) * 1000:.0f}\n")
            f.write(f"Success rate: {success_rate * 100:.0f}%\n")
        print(f"\n[saved] tests/results/perf_concurrent_upload.txt")


if __name__ == "__main__":
    asyncio.run(main())
