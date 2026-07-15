"""
Service Control (stop / status / doctor)
========================================
启服务改用 start_api.py / start_workers.py；
此脚本专注于：停止服务、查看状态、运行诊断、跑测试

用法：
  uv run python scripts/start_local.py stop       # 停全部
  uv run python scripts/start_local.py status     # 状态总览
  uv run python scripts/start_local.py doctor     # 完整健康诊断
  uv run python scripts/start_local.py test       # 跑 E2E 测试
  uv run python scripts/start_local.py restart    # 停全部 + 启 API
"""
import sys
import time
import json
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _start_lib import (  # noqa: E402
    C, info, ok, warn, err, section,
    LOGS, BACKEND, FRONTEND,
    UV, NPM, has_uv, VENV_PY,
    test_port, get_pids_on_port, read_pid_file, pid_alive, kill_pid,
    REDIS_HOST, REDIS_PORT, MYSQL_HOST, MYSQL_PORT, MINIO_PORT,
)


# ============================================================
#  Stop
# ============================================================
def stop_all():
    section("Stop all services")
    # 1) API (port 5000)
    info("Stopping API (port 5000)...")
    pid = read_pid_file(LOGS / "backend.pid")
    if pid and pid_alive(pid):
        kill_pid(pid)
        info(f"  killed backend PID={pid}")
    (LOGS / "backend.pid").unlink(missing_ok=True)
    for pid in get_pids_on_port(5000):
        kill_pid(pid)
        info(f"  killed port 5000 owner PID={pid}")

    # 2) Frontend (port 5173)
    info("Stopping frontend (port 5173)...")
    pid = read_pid_file(LOGS / "frontend.pid")
    if pid and pid_alive(pid):
        kill_pid(pid)
        info(f"  killed frontend PID={pid}")
    (LOGS / "frontend.pid").unlink(missing_ok=True)
    for pid in get_pids_on_port(5173):
        kill_pid(pid)
        info(f"  killed port 5173 owner PID={pid}")

    # 3) Celery
    info("Stopping Celery...")
    pid = read_pid_file(LOGS / "celery.pid")
    if pid and pid_alive(pid):
        kill_pid(pid)
        info(f"  killed celery PID={pid}")
    (LOGS / "celery.pid").unlink(missing_ok=True)

    # 4) Redis
    info(f"Stopping Redis (port {REDIS_PORT})...")
    pid = read_pid_file(LOGS / "redis.pid")
    if pid and pid_alive(pid):
        kill_pid(pid)
        info(f"  killed redis PID={pid}")
    (LOGS / "redis.pid").unlink(missing_ok=True)
    for pid in get_pids_on_port(REDIS_PORT):
        kill_pid(pid)
        info(f"  killed port {REDIS_PORT} owner PID={pid}")

    print()
    ok("All services stopped")


# ============================================================
#  Status
# ============================================================
def show_status():
    section("Service status")
    services = [
        ("API",      5000, LOGS / "backend.pid",  True),
        ("Frontend", 5173, LOGS / "frontend.pid", False),
        ("Celery",   0,    LOGS / "celery.pid",   False),
        ("Redis",    REDIS_PORT, LOGS / "redis.pid",    False),
        ("MySQL",    MYSQL_PORT, None,                  True),
        ("MinIO",    MINIO_PORT, None,                  False),
    ]
    for name, port, pid_file, required in services:
        running = port > 0 and test_port("127.0.0.1", port, 0.3)
        pid = read_pid_file(pid_file) if pid_file else None
        pid_alive_str = f"PID={pid}" if pid and pid_alive(pid) else ""
        if running:
            ok(f"{name:10s} :{port:<5}  RUNNING  {pid_alive_str}")
        else:
            if required:
                err(f"{name:10s} :{port:<5}  NOT running  (REQUIRED)")
            else:
                info(f"{name:10s} :{port:<5}  NOT running")
    # /api/health 探活
    if test_port("127.0.0.1", 5000):
        try:
            r = urllib.request.urlopen("http://127.0.0.1:5000/api/health", timeout=3)
            d = json.loads(r.read().decode())
            info(f"/api/health: status={d.get('status')}  env={d.get('env')}")
            for k, v in d.get("checks", {}).items():
                tag = "OK" if v.get("ok") else "FAIL"
                col = C.GREEN if v.get("ok") else C.RED
                print(f"  {col}  {k:10s}: {tag}{C.RESET}")
        except Exception as e:
            warn(f"/api/health 探活失败: {e}")


# ============================================================
#  Doctor
# ============================================================
def doctor():
    section("Doctor - full health diagnostic")
    info(f"Python: {sys.executable}")
    info(f"uv:    {UV or 'NOT FOUND'}")
    info(f"npm:   {NPM or 'NOT FOUND'}")
    info(f"Backend: {BACKEND}")
    info(f"Logs:    {LOGS}")
    print()
    # 依赖检查
    deps = [
        ("MySQL", MYSQL_HOST, MYSQL_PORT, True),
        ("Redis", REDIS_HOST, REDIS_PORT, False),
        ("MinIO", "127.0.0.1", MINIO_PORT, False),
    ]
    for name, host, port, required in deps:
        up = test_port(host, port)
        if up:
            ok(f"{name} {host}:{port}  UP")
        else:
            (err if required else warn)(f"{name} {host}:{port}  DOWN")
    # 后端健康
    if test_port("127.0.0.1", 5000):
        try:
            r = urllib.request.urlopen("http://127.0.0.1:5000/api/health", timeout=3)
            d = json.loads(r.read().decode())
            ok(f"Backend /api/health: {d.get('status')}")
        except Exception as e:
            err(f"Backend /api/health: {e}")
    else:
        warn("Backend :5000 not listening")


# ============================================================
#  Restart = stop + start API
# ============================================================
def restart():
    stop_all()
    time.sleep(2)
    info("Restarting API...")
    # 委托给 start_api
    import subprocess
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "start_api.py")],
    )
    return r.returncode


# ============================================================
#  main
# ============================================================
def main():
    args = [a.lower() for a in sys.argv[1:]]
    if not args:
        args = ["status"]
    if "stop" in args:
        return 0 if stop_all() is None else 0
    if "status" in args:
        show_status(); return 0
    if "doctor" in args:
        doctor(); return 0
    if "restart" in args:
        return restart()
    if "test" in args:
        # 委托给 scripts/run_e2e.py
        import subprocess
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "run_e2e.py")])
        return r.returncode
    err(f"Unknown command: {args}")
    info("Try: stop | status | doctor | restart | test")
    return 1


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        err("Interrupted by user")
        rc = 130
    raise SystemExit(rc)
