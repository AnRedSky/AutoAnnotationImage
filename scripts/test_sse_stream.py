"""E2E 验证 SSE 训练进度推送
==================================
1. 登录拿 token (用 admin)
2. 用第一个数据集启动训练
3. 走 SSE 流 /api/training/progress/stream/{task_id}
4. 解析每帧 data: 事件, 打印 (state, progress, msg)
5. 看到 SUCCESS/FAILURE/REVOKED 退出; 或超时 90s 强制退出
6. 返回: 0=SSE 端到端正常, 非零=异常
"""
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:5000"
TIMEOUT_S = 90


def http(method, path, *, token=None, data=None, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())


def login():
    """直接用 admin 登录 (不用每次注册新用户)"""
    r = httpx_post("/api/auth/login", data={"username": "admin", "password": "admin123"})
    return r["access_token"]


def httpx_post(path, *, data=None):
    """httpx 没装时的简易 form 替代 (login 是 form-encoded)"""
    if data is None:
        body = b""
    else:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())


def main():
    # 1) 登录拿 token
    tok = login()
    print(f"[1] logged in as admin, token len={len(tok)}")

    # 2) 找第一个数据集
    ds_list = http("GET", "/api/datasets", token=tok)
    items = ds_list.get("items") or ds_list or []
    if not items:
        print("NO DATASETS - abort")
        return 1
    ds_id = items[0]["id"]
    print(f"[2] using dataset_id={ds_id}, name={items[0].get('name')}")

    # 3) 启动训练 (2 epoch 加速, resnet18 小模型)
    r = http("POST", "/api/training/start", token=tok, params={
        "dataset_id": ds_id, "base_model": "resnet18",
        "model_name": f"sse_{int(time.time())}",
        "epochs": 2, "batch_size": 8, "learning_rate": 0.001,
    })
    task_id = r["task_id"]
    print(f"[3] training started, task_id={task_id}, state={r['state']}")

    # 4) 走 SSE 流
    print(f"[4] connecting to SSE: /api/training/progress/stream/{task_id[:8]}...")
    url = f"{BASE}/api/training/progress/stream/{task_id}?token={urllib.parse.quote(tok)}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "text/event-stream")
    req.add_header("Cache-Control", "no-cache")

    started = time.time()
    final_state = None
    last_summary = None
    frames = 0
    saw_progress = False
    saw_end_event = False
    content_type = ""
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT_S)
        content_type = resp.headers.get("Content-Type", "")
        print(f"    Content-Type: {content_type}")
        if "text/event-stream" not in content_type:
            print(f"    !! Not SSE: {content_type}")
            return 2

        event_name = None
        data_buf = []

        def flush():
            nonlocal event_name, data_buf, final_state, last_summary, frames
            nonlocal saw_progress, saw_end_event
            if not data_buf:
                event_name, data_buf = None, []
                return False
            raw = "\n".join(data_buf).strip()
            cur_event, data_buf = event_name, []
            event_name = None
            if not raw:
                return False
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return False
            frames += 1
            if cur_event == "end":
                saw_end_event = True
            summary = (payload.get("state"), payload.get("progress"),
                       payload.get("current_epoch"), payload.get("total_epochs"),
                       (payload.get("message") or "")[:40])
            if summary != last_summary:
                elapsed = time.time() - started
                print(f"    [{elapsed:5.1f}s] frame #{frames} "
                      f"{'(end-event)' if saw_end_event and frames == 1 else ''}: "
                      f"state={summary[0]:10s} progress={summary[1]:6.2f}% "
                      f"epoch={summary[2]}/{summary[3]} msg={summary[4]!r}")
                last_summary = summary
            if payload.get("state") == "PROGRESS":
                saw_progress = True
            if payload.get("state") in ("SUCCESS", "FAILURE", "REVOKED"):
                final_state = payload["state"]
                return True
            return False

        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="ignore").rstrip("\n").rstrip("\r")
            if line.startswith(":"):
                # 注释帧 (keepalive/connected), 跳过
                continue
            if line == "":
                if flush() is True:
                    break
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_buf.append(line[5:].lstrip())
        flush()
    except urllib.error.HTTPError as e:
        print(f"    HTTPError: {e.code} {e.read().decode()[:200]}")
        return 3
    except Exception as e:
        print(f"    EXC: {type(e).__name__}: {e}")

    # 5) 验证
    print()
    print(f"[5] FINAL: state={final_state} frames={frames} elapsed={time.time()-started:.1f}s")
    print(f"    Content-Type ok:        {'text/event-stream' in content_type}")
    print(f"    Saw end event:          {saw_end_event}")
    print(f"    Saw PROGRESS or final:  {saw_progress or final_state is not None}")
    print(f"    Reached terminal:       {final_state is not None}")
    # 任何 terminal state (SUCCESS/FAILURE/REVOKED) 都算 SSE 流程完整
    if final_state is not None and "text/event-stream" in content_type:
        return 0
    return 5


if __name__ == "__main__":
    sys.exit(main())

