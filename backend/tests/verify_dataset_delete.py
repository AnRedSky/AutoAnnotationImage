"""Test dataset deletion - clean."""
import sys, subprocess
from pathlib import Path
import httpx
import logging
logging.disable(logging.CRITICAL)

BASE = "http://127.0.0.1:5000"
PROJ = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation")

def log(t, m): print(f"[{t}] {m}", flush=True)
def fail(m): log("FAIL", m); sys.exit(1)
def ok(m): log("OK", m)

def dbq(code: str) -> str:
    r = subprocess.run(
        ["uv", "run", "--project", "backend", "python", "-c", code],
        cwd=str(PROJ), capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        return f"ERR: {r.stderr[-400:]}"
    return r.stdout.strip()

def main():
    log("START", "=== dataset 删除功能测试 ===")
    r = httpx.post(f"{BASE}/api/auth/login", data={"username":"admin","password":"admin123"})
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    ok("login")

    # TEST 1: 空 dataset 创建并删除
    log("TEST1", "--- 空 dataset ---")
    r = httpx.post(f"{BASE}/api/datasets", json={"name": "TEST_DEL_1"}, headers=H)
    nid = r.json()["id"]
    r = httpx.delete(f"{BASE}/api/datasets/{nid}", headers=H)
    log("INFO", f"DELETE empty -> {r.status_code} {r.text[:80]}")
    if r.status_code != 200: fail(f"empty delete: {r.status_code} {r.text[:200]}")
    r = httpx.get(f"{BASE}/api/datasets/{nid}", headers=H)
    log("INFO", f"GET deleted -> {r.status_code}")

    # TEST 2: 带 category 的 dataset 删除
    log("TEST2", "--- 带 categories 的 dataset ---")
    r = httpx.post(f"{BASE}/api/datasets", json={"name": "TEST_DEL_2"}, headers=H)
    ds2 = r.json()["id"]
    log("INFO", f"created id={ds2}")
    for cn in ["catA", "catB", "catC"]:
        rr = httpx.post(f"{BASE}/api/datasets/{ds2}/categories", json={"name": cn}, headers=H)
        log("INFO", f"  category {cn} -> {rr.status_code}")

    # 看删除前的 DB 状态
    pre = dbq(f"""
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as db:
        cats = (await db.execute(text("SELECT COUNT(*) FROM category WHERE dataset_id={ds2}"))).scalar()
        print(f"cats_before={{cats}}")
asyncio.run(main())
""")
    log("INFO", f"删除前: {pre}")

    r = httpx.delete(f"{BASE}/api/datasets/{ds2}", headers=H)
    log("INFO", f"DELETE with cats -> {r.status_code} {r.text[:200]}")

    post = dbq(f"""
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as db:
        ds = (await db.execute(text("SELECT COUNT(*) FROM dataset WHERE id={ds2}"))).scalar()
        cats = (await db.execute(text("SELECT COUNT(*) FROM category WHERE dataset_id={ds2}"))).scalar()
        print(f"ds={{ds}} cats={{cats}}")
asyncio.run(main())
""")
    log("INFO", f"删除后: {post}")

    # TEST 3: 真正带 image 的删除 (看 storage 文件清理)
    log("TEST3", "--- 带 image 的 dataset + 磁盘清理 ---")
    r = httpx.post(f"{BASE}/api/datasets", json={"name": "TEST_DEL_3"}, headers=H)
    ds3 = r.json()["id"]
    log("INFO", f"created id={ds3}")

    # 看现有 dataset 20 的 storage 路径结构
    storage = PROJ / "backend" / "storage"
    sample_files = list(storage.rglob("*.png"))[:5] if storage.exists() else []
    log("INFO", f"storage 目录 sample 文件:")
    for f in sample_files:
        log("INFO", f"  {f.relative_to(storage)} (size={f.stat().st_size})")

    # 创建一个真实 1x1 PNG 文件上传 (更逼真)
    real_png = bytes.fromhex(
        "89504E470D0A1A0A"  # PNG signature
        "0000000D49484452"  # IHDR length=13
        "0000000100000001"  # 1x1
        "08020000009077532DE"  # 8-bit RGB
        "0000000C4944415478DA636460600000000500017A6D59170000000049454E44AE426082"
    )
    r = httpx.post(f"{BASE}/api/images/upload",
                   files={"file": ("real.png", real_png, "image/png")},
                   data={"dataset_id": ds3}, headers=H, timeout=30)
    log("INFO", f"upload -> {r.status_code} {r.text[:200]}")
    img_id = r.json().get("id") if r.status_code in (200, 201) else None

    if img_id:
        # 看 image 的 storage_path
        ipath = dbq(f"""
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("SELECT storage_path, status FROM image WHERE id={img_id}"))
        row = r.first()
        if row:
            print(f"path={{row[0]}} status={{row[1]}}")
asyncio.run(main())
""")
        log("INFO", f"uploaded image: {ipath}")

        # 删除 dataset
        r = httpx.delete(f"{BASE}/api/datasets/{ds3}", headers=H)
        log("INFO", f"DELETE with image -> {r.status_code} {r.text[:200]}")

        # 检查 DB 残留
        residue = dbq(f"""
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as db:
        ds = (await db.execute(text("SELECT COUNT(*) FROM dataset WHERE id={ds3}"))).scalar()
        imgs = (await db.execute(text("SELECT COUNT(*) FROM image WHERE dataset_id={ds3}"))).scalar()
        try:
            ann = (await db.execute(text("SELECT COUNT(*) FROM annotation WHERE dataset_id={ds3}"))).scalar()
        except Exception:
            ann = "n/a"
        print(f"ds={{ds}} imgs={{imgs}} anns={{ann}}")
asyncio.run(main())
""")
        log("INFO", f"DB 残留: {residue}")

    # TEST 4: FK 关系 (谁引用 dataset)
    log("TEST4", "--- FK 关系 ---")
    fk = dbq(r'''
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import engine
async def main():
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE REFERENCED_TABLE_NAME = 'datasets' AND TABLE_SCHEMA = DATABASE()
        """))
        rows = list(r)
        if rows:
            for row in rows:
                print(f"{row[0]}.{row[1]} -> {row[2]}")
        else:
            print("(no FK refs)")
asyncio.run(main())
''')
    log("INFO", f"FK 引用 datasets: {fk if fk else '(无)'}")

    # TEST 5: 看 ModelVersion 表本身
    log("TEST5", "--- ModelVersion 结构 ---")
    mv = dbq("""
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import engine
async def main():
    async with engine.connect() as conn:
        r = await conn.execute(text("DESCRIBE model_version"))
        for row in r:
            print(f"  {row[0]} {row[1]}")
asyncio.run(main())
""")
    log("INFO", f"model_version columns: {mv}")

    log("DONE", "=== 测试完成 ===")

if __name__ == "__main__":
    main()
