"""Dataset 删除 - 关键分析. 检查 FK / 孤立记录 / 磁盘."""
import sys, subprocess
from pathlib import Path
import httpx
import logging
logging.disable(logging.CRITICAL)

BASE = "http://127.0.0.1:5000"
PROJ = Path(r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation")

def log(t, m): print(f"[{t}] {m}", flush=True)

def dbq(code: str) -> str:
    r = subprocess.run(
        ["uv", "run", "--project", "backend", "python", "-c", code],
        cwd=str(PROJ), capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        return f"ERR: {r.stderr[-500:]}"
    return r.stdout.strip()

def main():
    log("START", "=== dataset 删除 - 关键分析 ===")
    r = httpx.post(f"{BASE}/api/auth/login", data={"username":"admin","password":"admin123"})
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    log("OK", "login")

    # 0) baseline
    r = httpx.get(f"{BASE}/api/datasets", headers=H)
    ds_before = r.json().get("items", [])
    log("INFO", f"现有 datasets: {len(ds_before)}")

    # 1) 看所有表里引用 dataset_id 的列
    log("CHECK", "--- 哪些表有 dataset_id 字段 ---")
    fk = dbq(r'''
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import engine
async def main():
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT TABLE_NAME, COLUMN_NAME
            FROM information_schema.COLUMNS
            WHERE COLUMN_NAME = 'dataset_id' AND TABLE_SCHEMA = DATABASE()
        """))
        rows = list(r)
        for row in rows:
            print(f"  {row[0]}.{row[1]}")
asyncio.run(main())
''')
    log("INFO", f"含 dataset_id 列的表:\n{fk}")

    # 2) 看 model_version, training_job 的外键定义
    log("CHECK", "--- model_version, training_job 的 FK ---")
    fk_def = dbq(r'''
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import engine
async def main():
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME,
                   REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE()
            AND (TABLE_NAME='category' OR TABLE_NAME='image' OR TABLE_NAME='annotation'
                 OR TABLE_NAME='training_job' OR TABLE_NAME='model_version')
            AND REFERENCED_TABLE_NAME IS NOT NULL
        """))
        rows = list(r)
        for row in rows:
            print(f"  {row[0]}.{row[1]} ({row[2]}) -> {row[3]}.{row[4]}")
asyncio.run(main())
''')
    log("INFO", f"现有 FK 定义:\n{fk_def}")

    # 3) 用 dataset 20 做真实删除测试 (因为有真实 image/annotation/training_job/model_version)
    log("CHECK", "--- 用真实 dataset 20 测试删除 ---")
    r = httpx.get(f"{BASE}/api/datasets/20", headers=H)
    log("INFO", f"dataset 20: {r.json().get('name') if r.status_code == 200 else 'NOT FOUND'}")

    pre = dbq(r'''
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as db:
        c = await db.execute(text("SELECT COUNT(*) FROM category WHERE dataset_id=20"))
        i = await db.execute(text("SELECT COUNT(*) FROM image WHERE dataset_id=20"))
        a = await db.execute(text("SELECT COUNT(*) FROM annotation WHERE dataset_id=20"))
        t = await db.execute(text("SELECT COUNT(*) FROM training_job WHERE dataset_id=20"))
        m = await db.execute(text("SELECT COUNT(*) FROM model_version WHERE dataset_id=20"))
        print(f"category={c.scalar()} image={i.scalar()} annotation={a.scalar()} training_job={t.scalar()} model_version={m.scalar()}")
asyncio.run(main())
''')
    log("INFO", f"删除前 dataset 20: {pre}")

    # 4) 先尝试删除 dataset 20 (这是个高风险操作, 但要测就真测)
    log("RISK", "尝试删除 dataset 20 (实际有数据的)...")
    r = httpx.delete(f"{BASE}/api/datasets/20", headers=H)
    log("INFO", f"DELETE /api/datasets/20 -> {r.status_code}")
    log("INFO", f"body: {r.text[:300]}")

    # 5) 立刻看后端 stdout 里报错
    post = dbq(r'''
import logging
logging.disable(logging.CRITICAL)
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as db:
        ds = await db.execute(text("SELECT id, name FROM dataset WHERE id=20"))
        ds_row = ds.first()
        if not ds_row:
            print("dataset 20: DELETED")
        else:
            print(f"dataset 20: STILL EXISTS ({ds_row[1]})")
        c = await db.execute(text("SELECT COUNT(*) FROM category WHERE dataset_id=20"))
        i = await db.execute(text("SELECT COUNT(*) FROM image WHERE dataset_id=20"))
        t = await db.execute(text("SELECT COUNT(*) FROM training_job WHERE dataset_id=20"))
        m = await db.execute(text("SELECT COUNT(*) FROM model_version WHERE dataset_id=20"))
        print(f"残留: category={c.scalar()} image={i.scalar()} training_job={t.scalar()} model_version={m.scalar()}")
asyncio.run(main())
''')
    log("INFO", f"删除后:\n{post}")

    # 6) 看磁盘 storage 目录
    log("CHECK", "--- 磁盘 storage 残留 ---")
    storage = PROJ / "backend" / "storage"
    if storage.exists():
        files = list(storage.rglob("*"))
        pngs = [f for f in files if f.suffix.lower() in (".png", ".jpg", ".jpeg")]
        total_size = sum(f.stat().st_size for f in pngs if f.is_file()) // 1024
        log("INFO", f"storage 目录: {len(pngs)} 个图片文件, 总 {total_size} KB")
        # 列出 dataset 20 目录
        ds20_dirs = [d for d in storage.rglob("20") if d.is_dir()]
        for d in ds20_dirs[:3]:
            ds20_files = list(d.rglob("*.png")) + list(d.rglob("*.jpg"))
            log("INFO", f"  dataset 20 目录: {d.relative_to(storage)} ({len(ds20_files)} files)")
    else:
        log("INFO", f"storage 目录不存在: {storage}")

    # 7) 结论
    log("DONE", "=== 分析完成 ===")

if __name__ == "__main__":
    main()
