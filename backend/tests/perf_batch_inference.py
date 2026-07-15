"""
Performance Test: Batch AI Inference
====================================
1000 张图片批量推理 (CPU), 4 个预训练模型
"""
import asyncio
import io
import time
from pathlib import Path

import httpx
from PIL import Image


API_BASE = "http://127.0.0.1:8000"
USERNAME = "admin"
PASSWORD = "admin123"
DATASET_ID = 1
N = 1000
MODELS = ["efficientnet_b0", "resnet50", "convnext_tiny", "mobilenetv3_large_100"]


def make_png_bytes(seed: int) -> bytes:
    img = Image.new("RGB", (224, 224), color=(seed % 255, (seed * 3) % 255, (seed * 7) % 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def main():
    async with httpx.AsyncClient() as client:
        # 1) 登录
        login = await client.post(
            f"{API_BASE}/api/auth/login",
            data={"username": USERNAME, "password": PASSWORD},
        )
        if login.status_code != 200:
            print(f"[fail] login failed: {login.status_code}")
            return
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2) 先批量上传 N 张
        print(f"[upload] uploading {N} images first...")
        up_t0 = time.perf_counter()
        for i in range(0, N, 50):
            batch = [("files", (f"perf_{j:04d}.png", make_png_bytes(j), "image/png")) for j in range(i, min(i + 50, N))]
            r = await client.post(
                f"{API_BASE}/api/images/upload/{DATASET_ID}",
                headers=headers,
                files=batch,
                timeout=60.0,
            )
            if r.status_code not in (200, 201):
                print(f"[warn] batch {i} failed: {r.status_code}")
        up_total = time.perf_counter() - up_t0
        print(f"[upload] done in {up_total:.1f}s")

        # 3) 对每个模型跑批量预标注
        results_dir = Path("tests/results")
        results_dir.mkdir(parents=True, exist_ok=True)
        out_lines = ["model,seconds,throughput_imgs_per_s"]

        for model_name in MODELS:
            print(f"\n[inference] model={model_name}, N={N}")
            t0 = time.perf_counter()
            try:
                resp = await client.post(
                    f"{API_BASE}/api/auto-annotate/run",
                    headers=headers,
                    json={
                        "dataset_id": DATASET_ID,
                        "model_name": model_name,
                        "confidence_threshold": 0.0,  # 全部分流
                        "async_run": False,
                    },
                    timeout=600.0,
                )
                ok = resp.status_code in (200, 201)
            except Exception as e:
                print(f"[error] {e}")
                ok = False
            elapsed = time.perf_counter() - t0
            throughput = N / elapsed if elapsed > 0 else 0
            out_lines.append(f"{model_name},{elapsed:.2f},{throughput:.1f}")
            print(f"  -> {elapsed:.2f}s ({throughput:.1f} img/s) {'OK' if ok else 'FAIL'}")

        out_path = results_dir / "perf_batch_inference.csv"
        out_path.write_text("\n".join(out_lines), encoding="utf-8")
        print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
