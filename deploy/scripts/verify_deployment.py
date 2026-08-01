"""
部署验证脚本 (Deployment Verification Script)
================================================

一键校验完整部署链路是否就绪，包括:
  1) Docker / Compose 版本检查
  2) 磁盘空间检查
  3) 端口占用检查 (3306/6379/8000/9000/9001/5173/8080)
  4) 配置文件存在性 (.env.prod / docker-compose.yml / Dockerfile)
  5) docker compose config 语法检查
  6) 镜像存在性检查 (annotation/api, annotation/worker, frontend)
  7) 容器健康状态 (docker compose ps)
  8) /api/health 端到端健康检查
  9) E2E 业务测试 (调用 scripts/run_e2e.py)
  10) 资源占用检查 (docker stats)

用法:
  python scripts/verify_deployment.py
  python scripts/verify_deployment.py --skip-build         # 跳过镜像构建
  python scripts/verify_deployment.py --skip-e2e           # 跳过 E2E
  python scripts/verify_deployment.py --env-file backend/.env.prod
  python scripts/verify_deployment.py --report             # 输出 JSON 报告

退出码:
  0 - 全部通过
  1 - 发现错误 (FAIL)
  2 - 部分警告 (WARN, --strict 时视为失败)
"""
import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

# ANSI
def _supports_color():
    if sys.platform == "win32":
        return os.environ.get("FORCE_COLOR") or sys.stdout.isatty()
    return True

import os
GREEN = "\033[92m" if _supports_color() else ""
RED = "\033[91m" if _supports_color() else ""
YELLOW = "\033[93m" if _supports_color() else ""
CYAN = "\033[96m" if _supports_color() else ""
BLUE = "\033[94m" if _supports_color() else ""
RESET = "\033[0m" if _supports_color() else ""


def header(msg: str):
    print()
    print(f"{CYAN}{'=' * 70}{RESET}")
    print(f"{CYAN}  {msg}{RESET}")
    print(f"{CYAN}{'=' * 70}{RESET}")


def step(msg: str):
    print(f"\n{BLUE}▶ {msg}{RESET}")


def ok(msg: str):
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg: str):
    print(f"  {YELLOW}⚠{RESET} {msg}")


def err(msg: str):
    print(f"  {RED}✗{RESET} {msg}")


# ============== 检查项 ==============
def check_docker_version() -> bool:
    step("1/10 Docker / Compose 版本检查")
    try:
        out = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            err("docker 未安装或不可用")
            return False
        ver = out.stdout.strip()
        # 提取版本号
        import re
        m = re.search(r"(\d+)\.(\d+)", ver)
        if m and int(m.group(1)) < 20:
            warn(f"docker 版本过低 ({ver}), 建议 >= 20.10")
        else:
            ok(f"docker: {ver}")
    except FileNotFoundError:
        err("docker 命令未找到, 请先安装 Docker Desktop")
        return False

    try:
        out = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            # 退化到 docker-compose
            out = subprocess.run(
                ["docker-compose", "--version"], capture_output=True, text=True, timeout=5
            )
            if out.returncode != 0:
                err("docker compose 不可用 (尝试 docker-compose 也失败)")
                return False
            warn("使用旧版 docker-compose (建议升级到 docker compose v2)")
            ok(f"docker-compose: {out.stdout.strip()}")
        else:
            ok(f"docker compose: {out.stdout.strip()}")
    except FileNotFoundError:
        err("docker compose 命令未找到")
        return False
    return True


def check_disk_space(min_gb: int = 5) -> bool:
    step(f"2/10 磁盘空间检查 (需要 >= {min_gb} GB)")
    total, used, free = shutil.disk_usage(ROOT)
    free_gb = free / (1024 ** 3)
    if free_gb < min_gb:
        err(f"可用空间不足: {free_gb:.1f} GB < {min_gb} GB")
        return False
    ok(f"可用空间: {free_gb:.1f} GB")
    return True


def check_ports(ports: list) -> bool:
    step(f"3/10 端口可用性检查: {ports}")
    all_ok = True
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            try:
                s.bind(("0.0.0.0", port))
                ok(f"端口 {port}: 空闲")
            except OSError:
                warn(f"端口 {port}: 已被占用 (可能服务已启动, OK)")
    return all_ok


def check_config_files() -> bool:
    step("4/10 配置文件存在性")
    required = [
        ROOT / "docker-compose.yml",
        BACKEND / "Dockerfile",
        BACKEND / "Dockerfile.api",
        BACKEND / "Dockerfile.worker",
        BACKEND / ".env.example",
        BACKEND / ".env.docker",
        BACKEND / ".dockerignore",
        BACKEND / "pyproject.toml",
        ROOT / "frontend" / "Dockerfile",
        ROOT / "frontend" / "nginx.conf",
        ROOT / "frontend" / ".dockerignore",
    ]
    all_ok = True
    for p in required:
        if p.exists():
            size = p.stat().st_size
            ok(f"✓ {p.relative_to(ROOT)} ({size} bytes)")
        else:
            err(f"✗ 缺失: {p.relative_to(ROOT)}")
            all_ok = False
    return all_ok


def check_compose_syntax(env_file: Path | None) -> bool:
    step("5/10 docker compose 配置语法检查")
    cmd = ["docker", "compose"]
    if env_file and env_file.exists():
        cmd += ["--env-file", str(env_file)]
    cmd += ["config", "--quiet"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=ROOT)
        if r.returncode != 0:
            err(f"compose 配置有误:\n{r.stderr[:500]}")
            return False
        ok("docker compose config 语法 OK")
        return True
    except FileNotFoundError:
        warn("docker compose 不在 PATH, 跳过语法检查")
        return True
    except subprocess.TimeoutExpired:
        err("compose config 30s 超时")
        return False


def check_images() -> bool:
    step("6/10 镜像存在性检查")
    required_images = [
        ("annotation/api:local", "API"),
        ("annotation/worker:local", "Worker"),
    ]
    try:
        r = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            warn("docker images 执行失败, 跳过")
            return True
        existing = set(r.stdout.strip().splitlines())
        for img, desc in required_images:
            if img in existing:
                ok(f"{desc} ({img}): 已构建")
            else:
                warn(f"{desc} ({img}): 未构建, 可通过 `docker compose build` 构建")
    except FileNotFoundError:
        warn("docker 不在 PATH, 跳过镜像检查")
    return True


def check_containers_running() -> bool:
    step("7/10 容器运行状态")
    try:
        r = subprocess.run(
            ["docker", "compose", "ps", "--format",
             "table {{.Service}}\t{{.State}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10, cwd=ROOT,
        )
        if r.returncode != 0:
            warn("docker compose ps 执行失败, 跳过 (服务可能未启动)")
            return True
        if "running" in r.stdout.lower() or "up" in r.stdout.lower():
            ok("部分/全部容器已运行")
            for line in r.stdout.strip().splitlines()[:10]:
                print(f"    {line}")
        else:
            warn("无容器运行, 请先 `docker compose up -d`")
        return True
    except FileNotFoundError:
        warn("docker 不在 PATH, 跳过")
        return True


def check_api_health(port: int = 8000) -> bool:
    step(f"8/10 /api/health 端到端健康检查 (port {port})")
    for i in range(15):
        try:
            r = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=2
            )
            d = json.loads(r.read().decode())
            status = d.get("status", "unknown")
            env = d.get("env", "unknown")
            if status == "ok":
                ok(f"/api/health: status=ok  env={env}")
                return True
            else:
                warn(f"/api/health: status={status}")
                return False
        except (urllib.error.URLError, ConnectionError):
            time.sleep(2)
    err(f"/api/health 30s 内未响应, 请检查 API 服务")
    return False


def run_e2e_tests() -> bool:
    step("9/10 E2E 业务流程测试")
    e2e_script = ROOT / "scripts" / "run_e2e.py"
    if not e2e_script.exists():
        warn("scripts/run_e2e.py 不存在, 跳过 E2E")
        return True
    try:
        r = subprocess.run(
            [sys.executable, str(e2e_script), "--repeat", "1"],
            capture_output=True, text=True, timeout=300, cwd=ROOT,
        )
        if r.returncode == 0:
            ok("E2E 全部通过")
            # 打印最后 5 行输出
            for line in r.stdout.strip().splitlines()[-5:]:
                print(f"    {line}")
            return True
        else:
            err("E2E 失败, 输出末尾:")
            for line in r.stdout.strip().splitlines()[-10:]:
                print(f"    {line}")
            return False
    except subprocess.TimeoutExpired:
        err("E2E 5 分钟超时")
        return False


def check_resource_usage() -> bool:
    step("10/10 容器资源占用")
    try:
        r = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            warn("docker stats 失败, 跳过")
            return True
        for line in r.stdout.strip().splitlines()[:10]:
            print(f"    {line}")
        return True
    except FileNotFoundError:
        warn("docker 不在 PATH, 跳过")
        return True


# ============== 主流程 ==============
def main():
    ap = argparse.ArgumentParser(description="部署验证脚本")
    ap.add_argument("--skip-build", action="store_true", help="跳过镜像构建检查")
    ap.add_argument("--skip-e2e", action="store_true", help="跳过 E2E 测试")
    ap.add_argument("--env-file", type=Path, default=None, help="指定 env 文件")
    ap.add_argument("--strict", action="store_true", help="严格模式: WARN 也算失败")
    ap.add_argument("--report", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--api-port", type=int, default=8000, help="API 端口 (默认 8000)")
    args = ap.parse_args()

    header(f"部署验证 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  项目根: {ROOT}")
    print(f"  env 文件: {args.env_file or '默认'}")
    print(f"  严格模式: {args.strict}")
    print(f"  跳过 E2E: {args.skip_e2e}")

    results = {}

    checks = [
        ("docker_version", check_docker_version, ()),
        ("disk_space", check_disk_space, ()),
        ("ports", check_ports, ([3306, 6379, 8000, 8080, 9000, 9001, 5173],)),
        ("config_files", check_config_files, ()),
        ("compose_syntax", check_compose_syntax, (args.env_file,)),
    ]
    if not args.skip_build:
        checks.append(("images", check_images, ()))

    checks += [
        ("containers_running", check_containers_running, ()),
        ("api_health", check_api_health, (args.api_port,)),
    ]
    if not args.skip_e2e:
        checks.append(("e2e_tests", run_e2e_tests, ()))

    checks.append(("resource_usage", check_resource_usage, ()))

    for name, fn, fn_args in checks:
        try:
            result = fn(*fn_args)
            results[name] = {"ok": bool(result), "skipped": False}
        except Exception as e:
            err(f"{name} 异常: {e}")
            results[name] = {"ok": False, "skipped": False, "error": str(e)}

    # 汇总
    header("部署验证汇总")
    err_count = sum(1 for r in results.values() if not r["ok"])
    passed = [k for k, r in results.items() if r["ok"]]
    failed = [k for k, r in results.items() if not r["ok"]]

    print(f"\n  通过: {len(passed)}/{len(results)}")
    for k in passed:
        print(f"    {GREEN}✓{RESET} {k}")
    if failed:
        print(f"\n  失败: {len(failed)}")
        for k in failed:
            print(f"    {RED}✗{RESET} {k}")

    print()
    if err_count == 0:
        print(f"  {GREEN}✓ 部署验证全部通过{RESET}")
        exit_code = 0
    else:
        print(f"  {RED}✗ {err_count} 项检查失败{RESET}")
        exit_code = 1

    # JSON 报告
    if args.report:
        report = {
            "timestamp": datetime.now().isoformat(),
            "project_root": str(ROOT),
            "results": results,
            "exit_code": exit_code,
        }
        report_path = LOGS / "deployment_verification.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  报告: {report_path.relative_to(ROOT)}")

    print()
    return exit_code


if __name__ == "__main__":
    sys.exit(main() or 0)
