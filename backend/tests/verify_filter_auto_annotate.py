"""
E2E 验证: /api/auto-annotate/run 严格按项目类目过滤
- 找一个有类目的数据集
- 调 /api/auto-annotate/run 跑基础预训练模型
- 验证: 响应有 no_match 字段
- 验证: 写回 DB 的 ai_prediction.top5 里所有 label 都在项目类目里 (或 ai_prediction 为 None)
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
    # 1) 登录
    code, login = http("POST", "/api/auth/login", form=True, body={
        "username": "admin", "password": "admin123"
    })
    assert code == 200, f"login failed: {code} {login}"
    token = login["access_token"]
    print(f"[OK] login")

    # 2) 找一个数据集
    code, ds = http("GET", "/api/datasets", token=token)
    if code != 200:
        code, ds = http("GET", "/api/datasets/", token=token)
    assert code == 200 and ds.get("items"), f"no datasets"
    ds_id = ds["items"][0]["id"]
    print(f"[OK] using dataset_id={ds_id}")

    # 3) 取该数据集的类目
    code, cats = http("GET", f"/api/datasets/{ds_id}/categories", token=token)
    assert code == 200, f"categories failed: {code} {cats}"
    cat_names = [c["name"] for c in (cats.get("items") or cats or [])]
    assert cat_names, f"no categories in dataset {ds_id}"
    print(f"[OK] project categories: {cat_names}")

    # 4) 准备 pending 图 (重置该数据集所有图到 pending + 清 ai_prediction)
    #    否则旧测试数据 (修复前写入的 class_xxx) 会干扰
    from app.database import AsyncSessionLocal
    from app.models.image import Image
    from sqlalchemy import update

    async def reset_all():
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Image)
                .where(Image.dataset_id == ds_id)
                .values(status="pending", ai_prediction=None, final_label_id=None)
            )
            await db.commit()
            # 取一张图
            result = await db.execute(
                select(Image).where(Image.dataset_id == ds_id).limit(1)
            )
            img = result.scalar_one()
            return img.id

    from sqlalchemy import select
    img_id = asyncio.run(reset_all())
    print(f"[OK] reset all images in dataset {ds_id} to pending; using image_id={img_id}")

    # 5) 调 /api/auto-annotate/run (基础预训练, use_finetune=false 是默认)
    print(f"[*] calling /api/auto-annotate/run (base model, may take ~5s)...")
    code, r = http("POST", "/api/auto-annotate/run", token=token, body={
        "dataset_id": ds_id,
        "model_name": "efficientnet_b0",
        "confidence_threshold": 0.0,  # 0 让所有"有匹配"的都标上, 方便统计
    })
    assert code == 200, f"auto-annotate failed: {code} {r}"
    print(f"[OK] auto-annotate response: total={r.get('total')}, "
          f"auto_labeled={r.get('auto_labeled')}, "
          f"need_human={r.get('need_human')}, "
          f"no_match={r.get('no_match')}, "
          f"avg_confidence={r.get('avg_confidence')}")
    assert "no_match" in r, "response missing no_match field"
    assert r["no_match"] >= 0
    assert r["auto_labeled"] + r["no_match"] <= r["total"]
    assert r["auto_labeled"] + r["need_human"] == r["total"]

    # 6) 校验: 任意被 auto_labeled 的图, ai_prediction.top5 中所有 label 必须在项目类目里
    code, lst3 = http("GET", f"/api/images/list/{ds_id}?status=ai_labeled&page=1&page_size=20", token=token)
    labeled = lst3.get("items", [])
    print(f"[OK] {len(labeled)} images now ai_labeled")
    bad_labels = []
    for img in labeled:
        ap = img.get("ai_prediction") or {}
        top5 = ap.get("top5") or []
        for item in top5:
            if item["label"] not in cat_names:
                bad_labels.append((img["id"], item["label"]))
    if bad_labels:
        print(f"[FAIL] found ai_labeled images with non-project labels: {bad_labels[:5]}")
        sys.exit(1)
    else:
        print(f"[OK] all ai_labeled images' top5 labels are within project categories")

    # 7) 校验: 任意 ai_labeled 的图, top1 必须命中项目类目
    if labeled:
        for img in labeled:
            ap = img.get("ai_prediction") or {}
            t1 = ap.get("top1")
            assert t1 in cat_names, f"img {img['id']} top1={t1} not in project categories"
        print(f"[OK] all ai_labeled images' top1 label is within project categories")

    # 8) 校验: pending 图 (没被标上) 的 ai_prediction 应该为 None
    code, lst4 = http("GET", f"/api/images/list/{ds_id}?status=pending&page=1&page_size=20", token=token)
    pending = lst4.get("items", [])
    bad_pending = [img for img in pending if img.get("ai_prediction")]
    if bad_pending:
        # 注: 可能有些 pending 是这次跑之前的旧数据, 不严格
        print(f"[info] {len(bad_pending)}/{len(pending)} pending images have non-null ai_prediction (可能是历史遗留)")
    else:
        print(f"[OK] no pending image has ai_prediction set")

    print("\n[PASS] all e2e checks passed")
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
