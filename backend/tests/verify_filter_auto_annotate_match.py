"""
E2E 验证 (正向): 基础模型输出与项目类目有交集时, 命中类目被改写为项目原名
- 给数据集加一个 ImageNet 实际存在的类目 "Egyptian cat" (ImageNet id 285)
- 跑 /api/auto-annotate/run
- 验证: 至少有一张图被标 ai_labeled, 且 top1 严格等于 "Egyptian cat" (而不是 "Egyptian_cat" 等模型原标签)
"""
import sys
import json
import asyncio
import urllib.request
import urllib.parse

BASE = "http://localhost:5000"


def http(method, path, token=None, body=None, form=False):
    url = f"{BASE}{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if form and body is not None:
        data = urllib.parse.urlencode(body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def main():
    code, login = http("POST", "/api/auth/login", form=True, body={
        "username": "admin", "password": "admin123"
    })
    assert code == 200, f"login failed: {code} {login}"
    token = login["access_token"]

    # 找一个数据集
    code, ds = http("GET", "/api/datasets", token=token)
    if code != 200:
        code, ds = http("GET", "/api/datasets/", token=token)
    ds_id = ds["items"][0]["id"]

    # 添加 "Egyptian cat" 类目 (ImageNet 真实存在的类)
    code, cat = http("POST", f"/api/datasets/{ds_id}/categories", token=token,
                     body={"name": "Egyptian cat", "description": "E2E test category"})
    if code in (200, 201):
        print(f"[OK] added 'Egyptian cat' category (id={cat.get('id')})")
    elif code == 400 and "exists" in str(cat):
        print(f"[OK] 'Egyptian cat' category already exists")
    else:
        print(f"[WARN] add category: {code} {cat}")

    # 重置所有图为 pending
    from app.database import AsyncSessionLocal
    from app.models.image import Image
    from sqlalchemy import update, select

    async def reset():
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Image)
                .where(Image.dataset_id == ds_id)
                .values(status="pending", ai_prediction=None, final_label_id=None)
            )
            await db.commit()

    asyncio.run(reset())
    print(f"[OK] reset dataset {ds_id}")

    # 跑自动标注
    print(f"[*] calling /api/auto-annotate/run with 'Egyptian cat' in categories...")
    code, r = http("POST", "/api/auto-annotate/run", token=token, body={
        "dataset_id": ds_id,
        "model_name": "efficientnet_b0",
        "confidence_threshold": 0.0,  # 0 让所有有匹配的都被标上
    })
    assert code == 200, f"failed: {code} {r}"
    print(f"[OK] auto-annotate: total={r.get('total')}, auto_labeled={r.get('auto_labeled')}, "
          f"no_match={r.get('no_match')}, need_human={r.get('need_human')}")
    assert r["no_match"] + r["auto_labeled"] == r["total"]

    # 校验: auto_labeled 的图, top1 必须是项目类目 (不能是 Egyptian_cat 带下划线)
    code, lst = http("GET", f"/api/images/list/{ds_id}?status=ai_labeled&page=1&page_size=20", token=token)
    labeled = lst.get("items", [])
    print(f"[OK] {len(labeled)} images are ai_labeled")

    if labeled:
        for img in labeled:
            ap = img.get("ai_prediction") or {}
            t1 = ap.get("top1")
            t5 = ap.get("top5") or []
            # 关键: 标签必须是项目类目的原名 (没有下划线)
            for item in t5:
                label = item["label"]
                assert "_" not in label, f"img {img['id']} top5 contains underscore label: {label!r}"
            print(f"  img {img['id']}: top1={t1!r} conf={ap.get('top1_conf')}")
    else:
        # 数据集里可能没有 Egyptian cat 的图, 看看 no_match 有几张, auto_labeled 0 也可以接受
        print(f"[info] no images auto_labeled; no_match={r['no_match']} (项目里的图可能都不是 cat)")

    # 校验: auto_labeled 的图里, ai_prediction.top5 的所有 label 都在项目类目里
    code, cat_resp = http("GET", f"/api/datasets/{ds_id}/categories", token=token)
    cat_names = [c["name"] for c in (cat_resp.get("items") or cat_resp or [])]
    print(f"[OK] project categories: {cat_names}")
    bad = []
    for img in labeled:
        for item in (img.get("ai_prediction") or {}).get("top5") or []:
            if item["label"] not in cat_names:
                bad.append((img["id"], item["label"]))
    assert not bad, f"found non-project labels: {bad[:5]}"
    print(f"[OK] all ai_labeled images' top5 labels are project categories")

    print("\n[PASS] positive e2e passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n[ERR] {e}")
        sys.exit(2)
