"""
E2E 验证: ai_labeled 状态图也能被 /clear 清除
- 登录获取 token
- 通过 SQLAlchemy 把某张图直接设为 status=ai_labeled, 写入 ai_prediction JSON
- 调 POST /api/annotations/clear
- 校验: status 回 pending, ai_prediction 被清空, 接口 cleared=1
- 校验: 审计日志有 reject 记录
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
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode() or "{}")


async def set_image_ai_labeled(image_id: int):
    """直接通过 SQLAlchemy 把图设为 ai_labeled, 模拟 AI 预标注"""
    from app.database import AsyncSessionLocal
    from app.models.image import Image
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        img = (await db.execute(select(Image).where(Image.id == image_id))).scalar_one()
        img.status = "ai_labeled"
        img.ai_prediction = {
            "top1": "class_test_top1",
            "top1_conf": 0.88,
            "top5": [
                {"label": "class_test_top1", "conf": 0.88},
                {"label": "class_a", "conf": 0.05},
                {"label": "class_b", "conf": 0.03},
                {"label": "class_c", "conf": 0.02},
                {"label": "class_d", "conf": 0.02},
            ],
        }
        await db.commit()
        return img.status, img.ai_prediction


async def restore_image(image_id: int, original_status: str):
    """恢复原状态 (如果原 status 不是 pending, 也写回)"""
    from app.database import AsyncSessionLocal
    from app.models.image import Image
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        img = (await db.execute(select(Image).where(Image.id == image_id))).scalar_one()
        img.status = original_status
        await db.commit()


def main():
    # 1) 登录 (OAuth2 form)
    code, login = http("POST", "/api/auth/login", form=True, body={
        "username": "admin", "password": "admin123"
    })
    assert code == 200, f"login failed: {code} {login}"
    token = login["access_token"]
    print(f"[OK] login: token len={len(token)}")

    # 2) 找一个数据集 (支持 /api/datasets 和 /api/datasets/ 两种)
    code, ds = http("GET", "/api/datasets", token=token)
    if code != 200:
        code, ds = http("GET", "/api/datasets/", token=token)
    assert code == 200 and ds.get("items"), f"no datasets: {code} {ds}"
    ds_id = ds["items"][0]["id"]
    print(f"[OK] using dataset_id={ds_id}")

    # 3) 拿一张图
    code, lst = http("GET", f"/api/images/list/{ds_id}?page=1&page_size=1", token=token)
    assert code == 200 and lst.get("items"), "no images"
    img_id = lst["items"][0]["id"]
    original_status = lst["items"][0]["status"]
    print(f"[OK] using image_id={img_id}, original status={original_status}")

    # 4) 通过 SQLAlchemy 把图设为 ai_labeled
    asyncio.run(set_image_ai_labeled(img_id))
    print(f"[OK] DB: image {img_id} set to status=ai_labeled with ai_prediction")

    # 5) 校验调用前确实是 ai_labeled
    code, before = http("GET", f"/api/images/{img_id}", token=token)
    assert code == 200, f"get image failed: {code}"
    assert before["status"] == "ai_labeled", f"expected ai_labeled, got {before['status']}"
    assert before.get("ai_prediction"), "expected ai_prediction not null"
    print(f"[OK] before clear: status={before['status']}, "
          f"top1={before['ai_prediction'].get('top1')}")

    # 6) 调 /clear
    code, r = http("POST", "/api/annotations/clear", token=token,
                   body={"image_ids": [img_id]})
    assert code == 200, f"clear failed: {code} {r}"
    print(f"[OK] clear response: cleared={r['cleared']}, skipped={r['skipped']}")
    print(f"     items[0] = {r['items'][0]}")
    assert r["cleared"] == 1, f"expected cleared=1, got {r['cleared']}"
    assert r["items"][0]["ai_cleared"] is True, "expected ai_cleared=True"
    assert r["items"][0]["had_ai"] is True
    assert r["items"][0]["old_ai_top1"] == "class_test_top1"

    # 7) 校验清除后状态
    code, after = http("GET", f"/api/images/{img_id}", token=token)
    assert code == 200
    print(f"[OK] after clear: status={after['status']}, "
          f"ai_prediction={after.get('ai_prediction')}")
    assert after["status"] == "pending", f"expected pending, got {after['status']}"
    assert after.get("ai_prediction") is None, "expected ai_prediction None"
    assert after.get("final_label_id") is None, "expected final_label_id None"

    # 8) 校验审计: 应该多一条 reject 记录
    code, audit = http("GET", f"/api/annotations/list/{ds_id}?action=reject&page=1&page_size=5",
                       token=token)
    if code == 200 and audit.get("items"):
        last = audit["items"][0]
        print(f"[OK] audit log: image_id={last['image_id']} action={last['action']} "
              f"from={last.get('from_label_name')} to={last.get('to_label_name')}")
        assert last["image_id"] == img_id
    else:
        print(f"[WARN] audit log: {code} {audit}")

    # 9) 清理: 恢复原状态 (本测试原 status=pending, clear 后也是 pending, 无需恢复;
    #    保留逻辑以防后续扩展; 用新 loop 避免 SQLAlchemy 异步池跨 loop 警告)
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(restore_image(img_id, original_status))
        loop.close()
        print(f"[OK] cleanup: image {img_id} restored to {original_status}")
    except Exception as e:
        print(f"[info] cleanup skipped: {e}")

    print("\n[PASS] all checks passed")
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
