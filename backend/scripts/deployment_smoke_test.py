"""
部署烟雾测试 (Deployment Smoke Test)
====================================

部署后用 admin 账户直接登录, 验证全链路通畅。
相比 run_e2e.py:
  - 不需要注册新用户 (admin 已存在)
  - 覆盖核心业务流程, 不需要 Celery/模型下载
  - 更快更稳, 适合 CI / 部署后立即验证

用法:
  cd backend
  uv run python scripts/deployment_smoke_test.py                       # 默认 admin/admin123
  uv run python scripts/deployment_smoke_test.py --admin-user admin --admin-pwd admin123
  uv run python scripts/deployment_smoke_test.py --base-url http://localhost:8000
  uv run python scripts/deployment_smoke_test.py --report
"""
import argparse
import http.client
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
import struct
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

# ANSI
def _supports_color():
    return sys.stdout.isatty() or os.environ.get("FORCE_COLOR")

GREEN = "\033[92m" if _supports_color() else ""
RED = "\033[91m" if _supports_color() else ""
YELLOW = "\033[93m" if _supports_color() else ""
CYAN = "\033[96m" if _supports_color() else ""
BLUE = "\033[94m" if _supports_color() else ""
RESET = "\033[0m" if _supports_color() else ""


# ===== 测试状态 =====
passed = 0
failed = 0
skipped = 0
results = []


def section(msg):
    print(f"\n{CYAN}{'=' * 70}{RESET}")
    print(f"{CYAN}  {msg}{RESET}")
    print(f"{CYAN}{'=' * 70}{RESET}")


def step(msg):
    print(f"\n{BLUE}▶ {msg}{RESET}")


def ok(msg):
    global passed
    passed += 1
    results.append(("PASS", msg))
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg, detail=""):
    global failed
    failed += 1
    results.append(("FAIL", msg, detail))
    print(f"  {RED}✗{RESET} {msg}")
    if detail:
        print(f"    {RED}→ {detail[:200]}{RESET}")


def skip(msg):
    global skipped
    skipped += 1
    results.append(("SKIP", msg))
    print(f"  {YELLOW}⏭{RESET} {msg}")


# ============== HTTP helpers ==============
def http_json(method, path, base_url, headers=None, data=None, timeout=10):
    """发送 JSON 请求"""
    url = base_url + path
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        elif isinstance(data, str):
            body = data.encode("utf-8")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = data
    else:
        body = None
    try:
        with urllib.request.urlopen(req, data=body, timeout=timeout) as r:
            txt = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(txt)
            except Exception:
                return r.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, txt
    except Exception as e:
        return None, str(e)


def make_test_png(path, size=(32, 32), color=(120, 200, 80)):
    """生成一个最小 PNG 文件用于上传测试"""
    width, height = size
    raw = b""
    for y in range(height):
        raw += b"\x00"  # filter byte
        for x in range(width):
            raw += bytes(color)
    compressed = zlib.compress(raw)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xffffffff
        )

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", compressed))
        f.write(chunk(b"IEND", b""))


def upload_multipart(host, port, path, file_specs, token, boundary):
    """multipart 文件上传"""
    body = b""
    for fname, fdata in file_specs:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="files"; filename="{fname}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += fdata
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    conn = http.client.HTTPConnection(host, port, timeout=30)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", errors="replace")
    conn.close()
    try:
        return resp.status, json.loads(data)
    except Exception:
        return resp.status, data


# ============== 测试步骤 ==============
def parse_args():
    ap = argparse.ArgumentParser(description="部署烟雾测试")
    ap.add_argument("--base-url", default="http://127.0.0.1:5000", help="API base URL")
    ap.add_argument("--admin-user", default="admin", help="admin 用户名")
    ap.add_argument("--admin-pwd", default="admin123", help="admin 密码")
    ap.add_argument("--report", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--skip-upload", action="store_true", help="跳过上传测试")
    return ap.parse_args()


def run_tests(args):
    base = args.base_url.rstrip("/")
    print()
    print(f"{CYAN}{'=' * 70}{RESET}")
    print(f"{CYAN}  部署烟雾测试{RESET}")
    print(f"{CYAN}  Time: {datetime.now().isoformat()}{RESET}")
    print(f"{CYAN}  URL:  {base}{RESET}")
    print(f"{CYAN}  User: {args.admin_user}{RESET}")
    print(f"{CYAN}{'=' * 70}{RESET}")

    # ===== 1) 健康检查 =====
    section("1/9 健康检查")
    for i in range(15):
        code, data = http_json("GET", "/api/health", base, timeout=3)
        if code == 200:
            env_name = data.get("env", "?")
            ok(f"/api/health: status=ok  env={env_name}")
            checks = data.get("checks", {})
            for ck_name, ck_data in checks.items():
                if isinstance(ck_data, dict) and ck_data.get("ok"):
                    ok(f"  {ck_name}: OK")
                else:
                    fail(f"  {ck_name}: NOT OK", str(ck_data))
            break
        time.sleep(2)
    else:
        fail("/api/health 30s 未响应, 服务可能未启动")
        return False

    # ===== 2) Admin 登录 =====
    section("2/9 Admin 登录")
    code, data = http_json("POST", "/api/auth/login", base,
                            data=f"username={args.admin_user}&password={args.admin_pwd}")
    if code == 200 and data.get("access_token"):
        token = data["access_token"]
        user_id = data.get("user_id")
        ok(f"登录成功: user_id={user_id}  role=admin")
    else:
        fail(f"admin 登录失败: code={code}", str(data)[:200])
        return False
    headers = {"Authorization": f"Bearer {token}"}

    # ===== 3) 当前用户信息 =====
    section("3/9 当前用户信息")
    code, data = http_json("GET", "/api/auth/me", base, headers=headers)
    if code == 200 and data.get("username") == args.admin_user:
        ok(f"me: username={data['username']}  role={data.get('role')}")
    else:
        fail(f"me 失败: code={code}", str(data)[:200])

    # ===== 4) 数据集 CRUD =====
    section("4/9 数据集 CRUD")
    ds_name = f"smoke_ds_{int(time.time())}"
    code, data = http_json("POST", "/api/datasets", base, headers=headers, data={
        "name": ds_name, "description": "smoke test",
        "task_type": "classification",
        "category_names": ["cat_a", "cat_b"],
    })
    if code in (200, 201) and data.get("id"):
        dataset_id = data["id"]
        ok(f"创建数据集: id={dataset_id} name={ds_name}")
    else:
        fail(f"创建数据集失败: code={code}", str(data)[:200])
        return False

    code, data = http_json("GET", f"/api/datasets/{dataset_id}", base, headers=headers)
    if code == 200 and data.get("name") == ds_name:
        ok(f"获取数据集详情: name={data['name']}")
    else:
        fail(f"获取数据集失败: code={code}", str(data)[:200])

    code, data = http_json("GET", "/api/datasets", base, headers=headers)
    if code == 200:
        items = data if isinstance(data, list) else data.get("items", data.get("total", "?"))
        ok(f"列出数据集: count={len(items) if isinstance(items, list) else items}")
    else:
        fail(f"列出数据集失败: code={code}")

    code, data = http_json("GET", f"/api/datasets/{dataset_id}/categories", base, headers=headers)
    if code == 200:
        items = data.get("items", data) if isinstance(data, dict) else data
        cat_a = items[0]["id"] if items else None
        ok(f"列出类别: count={len(items)}")
    else:
        fail(f"列出类别失败: code={code}")

    # ===== 5) 图片上传 =====
    if args.skip_upload:
        skip("图片上传 (--skip-upload)")
    else:
        section("5/9 图片上传 (含去重)")
        host = base.split("://")[1].split(":")[0]
        port = int(base.split(":")[-1])

        # 生成临时 PNG
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        make_test_png(tmp.name)
        with open(tmp.name, "rb") as f:
            file_bytes = f.read()
        os.unlink(tmp.name)

        # 5a. 单张上传
        status, resp = upload_multipart(
            host, port, f"/api/images/upload/{dataset_id}",
            [("test.png", file_bytes)], token, "----SmokeBoundary111"
        )
        if status in (200, 201):
            uploaded = resp.get("uploaded", 0) if isinstance(resp, dict) else "?"
            ok(f"上传图片: status={status} uploaded={uploaded}")
        else:
            fail(f"上传图片失败: status={status}", str(resp)[:200])
            return False

        # 5b. 重复上传 (验证去重)
        status, resp = upload_multipart(
            host, port, f"/api/images/upload/{dataset_id}",
            [("test.png", file_bytes)], token, "----SmokeBoundary222"
        )
        if status in (200, 201) and isinstance(resp, dict):
            dup = resp.get("duplicates", 0)
            if dup > 0:
                ok(f"去重验证: duplicates={dup}")
            else:
                fail("去重未生效: duplicates=0", json.dumps(resp)[:200])
        else:
            fail(f"去重测试失败: status={status}", str(resp)[:200])

        # 5c. 列出图片
        code, data = http_json("GET", f"/api/images/list/{dataset_id}?page=1&page_size=10", base, headers=headers)
        if code == 200 and data.get("items"):
            first_img_id = data["items"][0]["id"]
            ok(f"列出图片: count={len(data['items'])}")
        else:
            fail(f"列出图片失败: code={code}", str(data)[:200])
            first_img_id = None

    # ===== 6) 标注 =====
    section("6/9 标注管理")
    if not args.skip_upload and first_img_id and cat_a:
        code, data = http_json("POST", "/api/annotations/save", base, headers=headers, data={
            "image_id": first_img_id, "label_id": cat_a,
            "time_spent_ms": 1500, "is_confirm": True,
        })
        if code in (200, 201):
            ok(f"保存标注: status={data.get('new_status', '?')}")
        else:
            fail(f"保存标注失败: code={code}", str(data)[:200])

    code, data = http_json("GET", f"/api/annotations/stats/{dataset_id}", base, headers=headers)
    if code == 200:
        ok(f"标注统计: total={data.get('total_annotations', 0)}")
    else:
        fail(f"标注统计失败: code={code}", str(data)[:200])

    # ===== 7) 系统统计 =====
    section("7/9 系统统计接口")
    code, data = http_json("GET", "/api/stats/overview", base, headers=headers)
    if code == 200:
        ok(f"overview: datasets={data.get('datasets', '?')} images={data.get('images', '?')}")
    else:
        fail(f"overview 失败: code={code}", str(data)[:200])

    code, data = http_json("GET", f"/api/stats/dataset/{dataset_id}", base, headers=headers)
    if code == 200:
        ok(f"dataset stats: statuses={len(data.get('status_counts', {}))}")
    else:
        fail(f"dataset stats 失败: code={code}", str(data)[:200])

    code, data = http_json("GET", "/api/auto-annotate/models", base, headers=headers)
    if code == 200:
        models = data if isinstance(data, list) else data.get("items", data.get("models", []))
        ok(f"AI 模型列表: count={len(models) if isinstance(models, list) else 'N/A'}")
    else:
        fail(f"AI 模型列表失败: code={code}", str(data)[:200])

    # ===== 8) 导出 =====
    section("8/9 标注导出")
    for fmt in ["csv"]:  # COCO/YOLO 需要训练数据, 仅测 CSV
        code, data = http_json("GET", f"/api/export/{fmt}/{dataset_id}", base, headers=headers, timeout=15)
        if code == 200:
            ok(f"导出 {fmt.upper()}: OK ({len(str(data))} chars)")
        else:
            fail(f"导出 {fmt.upper()} 失败: code={code}", str(data)[:200])

    # ===== 9) 模型版本 =====
    section("9/9 模型版本管理")
    code, data = http_json("GET", "/api/models/", base, headers=headers)
    if code == 200:
        items = data if isinstance(data, list) else data.get("items", [])
        ok(f"模型版本列表: count={len(items) if isinstance(items, list) else 'N/A'}")
    else:
        fail(f"模型版本列表失败: code={code}", str(data)[:200])

    return True


def main():
    args = parse_args()
    run_tests(args)

    # 汇总
    section("烟雾测试汇总")
    total = passed + failed + skipped
    print()
    print(f"  通过:   {passed} / {total}")
    print(f"  失败:   {failed} / {total}")
    print(f"  跳过:   {skipped} / {total}")
    print()

    if failed == 0:
        print(f"  {GREEN}✓ 烟雾测试全部通过 (PASSED){RESET}")
        rc = 0
    else:
        print(f"  {RED}✗ {failed} 项失败{RESET}")
        rc = 1

    if args.report:
        report = {
            "timestamp": datetime.now().isoformat(),
            "base_url": args.base_url,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "results": [
                r if len(r) == 2 else r
                for r in results
            ],
            "exit_code": rc,
        }
        report_path = LOGS / "deployment_smoke_test.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  报告: {report_path.relative_to(ROOT)}")

    print()
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[INFO] 中断")
        sys.exit(130)
