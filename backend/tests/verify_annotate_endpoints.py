"""
测试 /models/active 端点 + /images/list exclude_id 端点
"""
import json
import httpx

BASE = "http://127.0.0.1:5000"


def main():
    r = httpx.post(
        f"{BASE}/api/auth/login",
        data={"username": "admin", "password": "admin123"},
        timeout=5,
    )
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}

    # 1. 测试 /models/active
    print("=== /api/models/active ===")
    r = httpx.get(f"{BASE}/api/models/active", headers=H)
    print(f"  status={r.status_code}")
    print(f"  body={r.text[:400]}")
    if r.status_code == 200:
        body = r.json()
        if body.get("model"):
            print(f"  [OK] Active model: id={body['model']['id']} name={body['model']['name']} acc={body['model']['accuracy']:.2%}")
        else:
            print(f"  (no active model)")

    # 2. 测试 list with exclude_id
    print("\n=== /api/images/list/{dataset_id} with exclude_id ===")
    r = httpx.get(f"{BASE}/api/images/list/29?status=pending&page=1&page_size=3", headers=H)
    print(f"  baseline: status={r.status_code} total={r.json().get('total')} items={[i['id'] for i in r.json().get('items', [])]}")

    if r.json().get("items"):
        first_id = r.json()["items"][0]["id"]
        r2 = httpx.get(
            f"{BASE}/api/images/list/29",
            params={"status": "pending", "page": 1, "page_size": 3, "exclude_id": first_id},
            headers=H,
        )
        print(f"  exclude_id={first_id}: status={r2.status_code} total={r2.json().get('total')} items={[i['id'] for i in r2.json().get('items', [])]}")
        if r2.json().get("items"):
            new_ids = [i["id"] for i in r2.json()["items"]]
            if first_id not in new_ids:
                print(f"  [OK] exclude_id works: {first_id} excluded, new first = {new_ids[0]}")
            else:
                print(f"  [FAIL] exclude_id FAILED: {first_id} still in result")

    # 3. 测试 auto-label with model_id
    print("\n=== /api/images/auto-label/{dataset_id} with model_id ===")
    if r.json().get("items"):
        active = httpx.get(f"{BASE}/api/models/active", headers=H).json().get("model")
        if active:
            r3 = httpx.post(
                f"{BASE}/api/images/auto-label/29",
                params={
                    "use_finetune": True,
                    "model_id": active["id"],
                    "confidence_threshold": 0.5,
                },
                headers=H,
                timeout=60,
            )
            print(f"  status={r3.status_code}")
            if r3.status_code == 200:
                d = r3.json()
                print(f"  used_finetune={d.get('used_finetune')} model_name={d.get('model_name')} model_id={d.get('model_id')}")
                print(f"  total={d.get('total')} auto_labeled={d.get('auto_labeled')} need_human={d.get('need_human')} avg_conf={d.get('avg_confidence')}")
                if d.get('used_finetune') and d.get('model_id') == active['id']:
                    print(f"  [OK] model_id honored: {d.get('model_id')} matches active {active['id']}")
            else:
                print(f"  body={r3.text[:200]}")


if __name__ == "__main__":
    main()
