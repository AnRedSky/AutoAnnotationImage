"""真正的端到端联调：
1) 把 dataset 20 剩余 18 张图全部 confirm (status=human_confirmed, final_label_id=1)
2) 启动 Celery worker (前台, 日志可见)
3) 触发训练 (epochs=1 加快)
4) 看 worker 日志确认真训练
"""
import asyncio
import urllib.request, urllib.error, json, random, string, time, sys
import subprocess
from sqlalchemy import select, text
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.models.category import Category

BASE = "http://127.0.0.1:5000"

async def confirm_remaining_images():
    """把 dataset 20 所有未 confirm 的图批量设为 human_confirmed"""
    async with AsyncSessionLocal() as db:
        # 拿 cat 1 (随便一个非空 category)
        cat = (await db.execute(
            select(Category).where(Category.dataset_id == 20)
        )).scalars().first()
        cat_id = cat.id if cat else 1
        print(f"  Using category id={cat_id} (name={cat.name if cat else '?'})")

        # 拿所有未 confirm 的图
        imgs = (await db.execute(
            select(Image).where(
                Image.dataset_id == 20,
                Image.status != "human_confirmed",
            )
        )).scalars().all()
        print(f"  Need to confirm {len(imgs)} images")
        for img in imgs:
            img.status = "human_confirmed"
            img.final_label_id = cat_id
        await db.commit()
        # verify
        cnt = (await db.execute(text(
            "SELECT COUNT(*) FROM image WHERE dataset_id=20 AND status='human_confirmed'"
        ))).scalar()
        print(f"  Confirmed images now: {cnt}")
        return cat_id, cnt

def register_login():
    """注册 + 登录拿 token"""
    uname = "real_test_" + "".join(random.choices(string.ascii_lowercase, k=6))
    data = json.dumps({"username": uname, "password": "Test1234!", "email": uname + "@x.com"}).encode()
    req = urllib.request.Request(f"{BASE}/api/auth/register", data=data,
                                  headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    form = ("username=" + uname + "&password=Test1234!").encode()
    req = urllib.request.Request(f"{BASE}/api/auth/login", data=form)
    return json.loads(urllib.request.urlopen(req).read())["access_token"]

def trigger_training(token, dataset_id, cat_id):
    """调 POST /api/training/start 触发训练 (参数放 query)"""
    import http.client
    from urllib.parse import urlsplit
    params = (
        f"dataset_id={dataset_id}"
        f"&base_model=resnet18"
        f"&model_name=real_test_v1"
        f"&epochs=1"
        f"&batch_size=4"
        f"&learning_rate=0.001"
    )
    u = urlsplit(f"{BASE}/api/training/start?{params}")
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    conn.request("POST", u.path + "?" + u.query, body=b"{}",
                 headers={
                     "Authorization": "Bearer " + token,
                     "Content-Type": "application/json",
                 })
    r = conn.getresponse()
    return json.loads(r.read())

def main():
    print("=== Step 1: Confirm all remaining images in dataset 20 ===")
    cat_id, confirmed_count = asyncio.run(confirm_remaining_images())
    print()

    print("=== Step 2: Register + Login ===")
    token = register_login()
    print(f"  token len = {len(token)}")
    print()

    print("=== Step 3: Start Celery worker (foreground for 90s) ===")
    print("  starting worker in background...")
    log = open(r"D:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\logs\worker_test.log", "wb")
    # 用 detached 模式启 worker
    p = subprocess.Popen(
        ["uv", "run", "python", "start_workers.py", "--detach"],
        cwd=r"D:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend",
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        creationflags=0x8,
    )
    p.wait()
    print(f"  worker detached, log open")
    print("  waiting 5s for worker ready...")
    time.sleep(5)
    print()

    print("=== Step 4: Trigger training (epochs=1) ===")
    t0 = time.time()
    res = trigger_training(token, 20, cat_id)
    print(f"  /api/training/start response: {json.dumps(res, indent=2)[:300]}")
    task_id = res.get("task_id") or res.get("id") or res.get("job_id")
    if not task_id:
        print(f"  no task_id in response, exit"); return
    print(f"  task_id = {task_id}")
    print()

    print("=== Step 5: Poll progress ===")
    for i in range(60):  # poll 60s
        time.sleep(2)
        try:
            r = urllib.request.urlopen(
                f"{BASE}/api/training/progress/{task_id}?token={token}",
                timeout=5,
            )
            prog = json.loads(r.read())
            state = prog.get("state", "?")
            progress = prog.get("progress", 0)
            msg = prog.get("msg", "")
            print(f"  [{i*2:3d}s] state={state} progress={progress:.1f}% msg={msg[:60]}")
            if state in ("SUCCESS", "FAILURE"):
                print(f"\n  FINAL: {json.dumps(prog, indent=2)[:600]}")
                break
        except Exception as e:
            print(f"  [{i*2:3d}s] poll err: {e}")
    else:
        print("  TIMEOUT 120s")

    elapsed = time.time() - t0
    print(f"\n  Total wall time: {elapsed:.1f}s")

    print()
    print("=== Step 6: Inspect worker log (last 30 lines) ===")
    log.close()
    with open(r"D:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\logs\worker_test.log", "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-8", errors="replace")
    except:
        text = data.decode("gbk", errors="replace")
    lines = text.splitlines()
    for line in lines[-30:]:
        print(f"  {line[:150]}")

main()
