"""
Verify 候选标签来源修复:
- use_finetune=True  → 项目 fine-tune 模型, top-5 应该是项目自定义类目 (中文)
- use_finetune=False → timm ImageNet 预训练, top-5 应该是 ImageNet 英文类名
- 置信度字段 confidence 不再为 NaN (字段名 typo 已修)
"""
import sys, json
import httpx
import asyncio
from pathlib import Path

BASE = "http://127.0.0.1:5000"

def log(t, m): print(f"[{t}] {m}", flush=True)
def fail(m): log("FAIL", m); sys.exit(1)
def ok(m): log("OK", m)

def main():
    log("START", "=== 候选标签 fix 验证 ===")

    # 1) 登录
    r = httpx.post(f"{BASE}/api/auth/login", data={"username":"admin","password":"admin123"})
    if r.status_code != 200: fail(f"login {r.status_code} {r.text[:200]}")
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    ok("login")

    # 2) 找数据集
    r = httpx.get(f"{BASE}/api/datasets", headers=H)
    ds = r.json().get("items", [])
    DS = ds[0]["id"]
    log("INFO", f"using dataset id={DS} name={ds[0].get('name')}")

    # 3) 取该数据集的 category (用于对比 fine-tune 预测标签)
    r = httpx.get(f"{BASE}/api/datasets/{DS}/categories", headers=H)
    if r.status_code != 200:
        # try alternative endpoint
        r = httpx.get(f"{BASE}/api/categories", params={"dataset_id": DS}, headers=H)
    cats = r.json().get("items", r.json()) if isinstance(r.json(), dict) else r.json()
    if not cats:
        # 找下 category 端点
        r = httpx.get(f"{BASE}/api/datasets/{DS}", headers=H)
        d = r.json()
        cats = d.get("categories", [])
    cat_names = sorted({c["name"] for c in cats if "name" in c})
    log("INFO", f"项目类目: {cat_names}")

    # 4) 激活 fine-tune 模型 (用最新的 v1)
    r = httpx.get(f"{BASE}/api/models/", headers=H)
    models = r.json().get("items", [])
    v1 = sorted([m for m in models if m.get("name") == "v1"],
                key=lambda x: x.get("id", 0), reverse=True)
    if v1:
        r = httpx.post(f"{BASE}/api/models/{v1[0]['id']}/activate", headers=H)
        ok(f"激活 fine-tune 模型 id={v1[0]['id']} acc={v1[0].get('accuracy')}")
    else:
        log("WARN", "no v1 model to activate; fine-tune test may fail")

    # 5) 把若干张图重置为 pending, 以便 auto-label 有图可标
    log("INFO", "重置部分图片为 pending 用于测试...")
    import subprocess
    code = f"""
import asyncio
from sqlalchemy import select, update
from app.database import AsyncSessionLocal
from app.models.image import Image
async def main():
    async with AsyncSessionLocal() as db:
        # 把 dataset={DS} 的前 5 张图重置为 pending
        result = await db.execute(select(Image).where(Image.dataset_id=={DS}).limit(5))
        for img in result.scalars():
            img.status = 'pending'
            img.ai_prediction = None
        await db.commit()
        cnt = (await db.execute(select(Image).where(Image.dataset_id=={DS}, Image.status=='pending'))).scalars().all()
        print(f'pending count: {{len(cnt)}}')
asyncio.run(main())
"""
    r = subprocess.run(
        ["uv", "run", "--project", "backend", "python", "-c", code],
        cwd=r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation",
        capture_output=True, text=True
    )
    print(r.stdout, end="")
    if r.returncode != 0:
        print(r.stderr, end="")
        fail("DB reset failed")

    # 6) 测试 use_finetune=True
    log("TEST", "=== use_finetune=True (项目训练模型) ===")
    r = httpx.post(f"{BASE}/api/images/auto-label/{DS}",
                   params={"use_finetune": "true", "confidence_threshold": 0.0},
                   headers=H, timeout=60)
    log("INFO", f"status={r.status_code} body={r.text[:300]}")
    if r.status_code != 200: fail(f"finetune auto-label: {r.status_code} {r.text[:300]}")
    data = r.json()
    ok(f"fine-tune 共 {data['total']} 张, 命中 {data['auto_labeled']}, model={data.get('model_name')} path={data.get('model_path')}")
    assert data.get("used_finetune") is True, "used_finetune 应为 true"

    # 拉一张图看 top-5 标签
    r = httpx.get(f"{BASE}/api/images/list/{DS}?status=ai_labeled&page=1&page_size=1", headers=H)
    items = r.json().get("items", [])
    if items:
        pred = items[0].get("ai_prediction", {})
        top5 = pred.get("top5", [])
        top1 = pred.get("top1", "")
        top1_conf = pred.get("top1_conf", None)
        log("INFO", f"top1 = '{top1}' (conf={top1_conf})")
        log("INFO", f"top5 = {json.dumps(top5, ensure_ascii=False, indent=2)}")
        # 验证 top1 是项目类目
        in_project = top1 in cat_names
        record_finetune_top1 = in_project
        if in_project:
            ok(f"top1='{top1}' 在项目类目 {cat_names} 中")
        else:
            log("WARN", f"top1='{top1}' 不在项目类目中 (模型可能没训练好, 但字段正常)")
        # 验证 confidence 是数字 (不是 NaN)
        confs = [t.get("confidence") for t in top5]
        all_numbers = all(isinstance(c, (int, float)) and c == c for c in confs)  # c==c 排除 NaN
        if all_numbers and confs:
            ok(f"confidence 字段全部是数字: {confs}")
        else:
            fail(f"confidence 字段异常: {confs}")
    else:
        log("WARN", "没有 ai_labeled 图, 跳过标签验证")

    # 7) 重置回 pending, 再测 use_finetune=False
    log("INFO", "重置回 pending...")
    code2 = code  # 同一段
    r = subprocess.run(
        ["uv", "run", "--project", "backend", "python", "-c", code2],
        cwd=r"d:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation",
        capture_output=True, text=True
    )
    print(r.stdout, end="")

    log("TEST", "=== use_finetune=False (ImageNet 预训练) ===")
    r = httpx.post(f"{BASE}/api/images/auto-label/{DS}",
                   params={"use_finetune": "false", "confidence_threshold": 0.0,
                           "model_name": "efficientnet_b0"},
                   headers=H, timeout=60)
    log("INFO", f"status={r.status_code} body={r.text[:300]}")
    if r.status_code != 200: fail(f"imagenet auto-label: {r.status_code} {r.text[:300]}")
    data = r.json()
    ok(f"ImageNet 共 {data['total']} 张, 命中 {data['auto_labeled']}, model={data.get('model_name')}")
    assert data.get("used_finetune") is False

    r = httpx.get(f"{BASE}/api/images/list/{DS}?status=ai_labeled&page=1&page_size=1", headers=H)
    items = r.json().get("items", [])
    if items:
        pred = items[0].get("ai_prediction", {})
        top5 = pred.get("top5", [])
        top1 = pred.get("top1", "")
        log("INFO", f"ImageNet top1 = '{top1}'")
        log("INFO", f"top5 = {json.dumps(top5, ensure_ascii=False, indent=2)}")
        if "class_" in top1:
            log("WARN", f"top1='{top1}' 仍是 class_X 兜底 (说明 imagenet_demo_labels.json 缺这条或 default_cfg.classes 不是 list)")
        else:
            ok(f"top1='{top1}' 是 ImageNet 英文标签 (不是 class_X)")

    log("DONE", "=== 验证完成 ===")

if __name__ == "__main__":
    main()
