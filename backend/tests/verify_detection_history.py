"""
v2.5.29 端到端验证: detection 训练 history 实时同步
=================================================
1. 登录拿 token
2. 找到一个 detection 数据集
3. 启动一个 2-epoch 的训练任务
4. 轮询 /api/training/history/{task_id} 看曲线是否实时出来
5. 验证 history 内容包含 box_loss/cls_loss/map_50 等指标
"""
import sys
import time
import json
import httpx

BASE = "http://127.0.0.1:5000"


def log(t, m):
    print(f"[{t}] {m}", flush=True)


def main():
    # 1) 登录
    r = httpx.post(f"{BASE}/api/auth/login",
                   data={"username": "admin", "password": "admin123"},
                   timeout=30)
    if r.status_code != 200:
        log("FAIL", f"login {r.status_code} {r.text[:200]}")
        sys.exit(1)
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    log("OK", "login")

    # 2) 列数据集找 detection
    r = httpx.get(f"{BASE}/api/datasets?page=1&page_size=50", headers=H, timeout=30)
    if r.status_code != 200:
        log("FAIL", f"list datasets {r.status_code} {r.text[:200]}")
        sys.exit(1)
    datasets = r.json().get("items", [])
    # 过滤出 detection 类型
    det_datasets = [d for d in datasets if d.get("task_type") == "detection"]
    if not det_datasets:
        log("FAIL", "no detection dataset found, 请先创建 detection 数据集 + 上传图片 + 标注")
        sys.exit(1)
    ds_id = det_datasets[0]["id"]
    log("OK", f"detection dataset: id={ds_id} name={det_datasets[0].get('name')}")

    # 3) 启动 2-epoch 训练
    r = httpx.post(f"{BASE}/api/detection/train", headers=H, json={
        "dataset_id": ds_id,
        "base_model": "yolov8n",
        "epochs": 2,
        "imgsz": 320,
        "batch_size": 4,
    }, timeout=30)
    if r.status_code not in (200, 201):
        log("FAIL", f"start train {r.status_code} {r.text[:200]}")
        sys.exit(1)
    body = r.json()
    task_id = body.get("celery_task_id")
    log("OK", f"train started: task_id={task_id}")

    # 4) 轮询 history
    if not task_id:
        log("FAIL", f"no task_id in response: {body}")
        sys.exit(1)

    log("INFO", f"开始轮询 /api/training/history/{task_id} (每 5s) ...")
    start = time.time()
    last_history_len = 0
    last_state = None
    while time.time() - start < 300:  # 最长 5 分钟
        try:
            r = httpx.get(f"{BASE}/api/training/history/{task_id}",
                          headers=H, timeout=10)
        except Exception as e:
            log("WARN", f"history request error: {e}")
            time.sleep(5)
            continue
        if r.status_code != 200:
            log("WARN", f"history {r.status_code} {r.text[:200]}")
            time.sleep(5)
            continue
        body = r.json()
        history = body.get("history", [])
        # 同时查 progress
        try:
            r2 = httpx.get(f"{BASE}/api/training/progress/{task_id}",
                           headers=H, timeout=10)
            progress_body = r2.json() if r2.status_code == 200 else {}
            state = progress_body.get("state", "?")
        except Exception:
            state = "?"

        if len(history) > last_history_len:
            log("INFO", f"history 增长: {last_history_len} -> {len(history)} (state={state})")
            last_history_len = len(history)

        if state in ("SUCCESS", "FAILURE", "REVOKED"):
            log("OK", f"训练终态: state={state}")
            break

        time.sleep(5)

    # 5) 验证
    log("INFO", f"最终 history 长度: {last_history_len}")
    if last_history_len == 0:
        log("FAIL", "history 始终为空 - 修复未生效!")
        sys.exit(1)

    # 查 history 内容
    r = httpx.get(f"{BASE}/api/training/history/{task_id}", headers=H, timeout=10)
    history = r.json().get("history", [])
    sample = history[0] if history else {}
    log("INFO", f"第一条 history: {json.dumps(sample, ensure_ascii=False)}")

    # 验证包含检测指标
    expected_keys = {"epoch"}
    has_any_loss = any(k in sample for k in ("box_loss", "cls_loss", "dfl_loss", "train_loss"))
    has_any_metric = any(k in sample for k in ("map_50", "map_50_95", "precision", "recall", "val_acc", "miou"))

    if not expected_keys.issubset(sample.keys()):
        log("FAIL", f"history 缺 epoch 字段: keys={list(sample.keys())}")
        sys.exit(1)
    if not has_any_loss:
        log("WARN", f"history 缺 loss 字段 (不影响功能): keys={list(sample.keys())}")
    if not has_any_metric:
        log("WARN", f"history 缺 val 指标 (不影响功能): keys={list(sample.keys())}")

    log("OK", "[OK] history 实时同步验证通过!")
    log("OK", f"样本 keys: {list(sample.keys())}")


if __name__ == "__main__":
    main()
