"""
Start Celery Worker
===================
启动 Celery worker（后台异步任务处理）

用法：
  cd backend
  uv run python start_workers.py                          # 前台模式（Ctrl+C 退出）
  uv run python start_workers.py --detach                 # 后台 detached 模式
  uv run python start_workers.py --stop                   # 停 worker
  uv run python start_workers.py --status                 # 查状态
  uv run python start_workers.py --loglevel debug         # 改日志级别

自动行为：
  - 检查 Redis 是否运行（未运行尝试从 PATH 或常见位置启动 redis-server.exe）
  - detached 模式下：父进程退出不影响 worker
  - PID 写入 logs/celery.pid

停止 detached worker：
  uv run python start_workers.py --stop
"""
import os
import sys
import time
import socket
import shutil
import subprocess
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
LOGS_DIR = BACKEND_DIR.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
PID_FILE = LOGS_DIR / "celery.pid"
LOG_FILE = LOGS_DIR / "celery.log"

# 切到 backend/ 目录（让 .env / pyproject.toml / app/ 都在正确位置）
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

# 颜色（ANSI）
class C:
    RESET = "\033[0m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"


def info(msg): print(f"  {C.YELLOW}[INFO]{C.RESET} {msg}")
def ok(msg):   print(f"  {C.GREEN}[OK]{C.RESET}   {msg}")
def err(msg):  print(f"  {C.RED}[ERR]{C.RESET}  {msg}")


# ============================================================
#  Redis 自动检测与启动
# ============================================================
def parse_env_value(key: str, default: str) -> str:
    """从 backend/.env 简单读一个 key（避免 import settings 早于此模块）"""
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        return default
    for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return default


REDIS_HOST = parse_env_value("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(parse_env_value("REDIS_PORT", "6379"))


def test_port(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def find_redis_exe() -> str | None:
    """定位 redis-server.exe"""
    for name in ("redis-server.exe", "redis-server"):
        p = shutil.which(name)
        if p:
            return p
    for path in [
        r"D:\ProgramFiles\DatabaseServer\Redis-x64-5.0.14.1\redis-server.exe",
        r"D:\Program Files\Redis\redis-server.exe",
        r"C:\Program Files\Redis\redis-server.exe",
        r"C:\Redis\redis-server.exe",
        r"D:\Redis\redis-server.exe",
    ]:
        if Path(path).is_file():
            return path
    return None


def ensure_redis() -> bool:
    """确保 Redis 在跑（先检测，未跑则启动）"""
    if test_port(REDIS_HOST, REDIS_PORT):
        ok(f"Redis :{REDIS_PORT} already running")
        return True
    info(f"Redis :{REDIS_PORT} not running, starting...")
    exe = find_redis_exe()
    if not exe:
        err("redis-server.exe not found in PATH or common locations")
        return False
    log = open(LOGS_DIR / "redis.log", "ab", buffering=0)
    try:
        p = subprocess.Popen(
            [exe, "--port", str(REDIS_PORT), "--bind", REDIS_HOST, "--save", "",
             "--dbfilename", f"dump-{REDIS_PORT}.rdb"],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            creationflags=0x00000008,  # DETACHED_PROCESS
            close_fds=True,
        )
        log.close()
        # Wait for port
        for _ in range(15):
            if test_port(REDIS_HOST, REDIS_PORT):
                ok(f"Redis started (PID={p.pid}, port={REDIS_PORT})")
                return True
            time.sleep(1)
        err(f"Redis port {REDIS_PORT} not listening after 15s")
        return False
    except Exception as e:
        err(f"Failed to start Redis: {e}")
        return False


# ============================================================
#  PID file
# ============================================================
def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        v = PID_FILE.read_text(encoding="ascii").strip()
        return int(v) if v else None
    except Exception:
        return None


def write_pid(pid: int):
    PID_FILE.write_text(str(pid), encoding="ascii")


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in out.stdout
    except Exception:
        return False


def kill_pid(pid: int):
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


# ============================================================
#  Celery 启动
# ============================================================
def start_foreground():
    """前台模式启动 Celery worker"""
    section("Starting Celery worker (foreground)")
    if not ensure_redis():
        err("Redis unavailable; Celery cannot start")
        return 1
    # 关键: 必须 import tasks 模块以触发 @celery_app.task 装饰器,
    # 否则 train_model_task / auto_annotate_task 不会注册到 celery_app.tasks,
    # worker 收到任务后会报 KeyError: 'app.workers.tasks.train_model_task'。
    # celery_app.py 的 include=['app.workers.tasks'] 已经处理了大部分情况,
    # 但 start_workers.py 是 worker_main 的直接调用方, 显式 import 更稳。
    from app.workers.celery_app import celery_app
    from app.workers import tasks as _tasks  # noqa: F401  (register tasks)
    loglevel = "info"
    if "--loglevel" in sys.argv:
        i = sys.argv.index("--loglevel")
        if i + 1 < len(sys.argv):
            loglevel = sys.argv[i + 1]
    ok(f"Celery broker: {celery_app.conf.broker_url}")
    print()
    try:
        celery_app.worker_main([
            "worker",
            f"--loglevel={loglevel}",
            "--pool=solo",  # Windows 上必须用 solo 池
            "--concurrency=1",
        ])
    except KeyboardInterrupt:
        print("\n[INFO] Celery worker stopped")
    return 0


def start_detach():
    """detached 模式启动（关掉父进程不影响 worker）"""
    section("Starting Celery worker (detached)")
    if not ensure_redis():
        err("Redis unavailable; Celery cannot start")
        return 1

    # Check if already running
    existing = read_pid()
    if existing and pid_alive(existing):
        ok(f"Celery already running (PID={existing})")
        return 0
    PID_FILE.unlink(missing_ok=True)

    # 构造 detached 启动参数
    argv = [
        sys.executable, str(Path(__file__).resolve()),  # 重新跑本脚本，不带 --detach
        "--loglevel", "info",
    ]
    # 透传 --loglevel
    if "--loglevel" in sys.argv:
        i = sys.argv.index("--loglevel")
        if i + 1 < len(sys.argv):
            argv[3] = sys.argv[i + 1]

    log = open(LOG_FILE, "ab", buffering=0)
    err_log = open(LOG_FILE, "ab", buffering=0)  # 同一文件
    try:
        p = subprocess.Popen(
            argv,
            cwd=str(BACKEND_DIR),
            stdout=log, stderr=err_log, stdin=subprocess.DEVNULL,
            creationflags=0x00000008,  # DETACHED_PROCESS
            close_fds=True,
        )
        log.close()
        err_log.close()
        write_pid(p.pid)
        ok(f"Celery launcher PID={p.pid}")
        info(f"Logs: {LOG_FILE}")
        # Wait 3s 确认没立即退出
        time.sleep(3)
        if pid_alive(p.pid):
            ok("Celery running")
            return 0
        err(f"Celery exited immediately; check {LOG_FILE}")
        PID_FILE.unlink(missing_ok=True)
        return 1
    except Exception as e:
        err(f"Failed to start Celery: {e}")
        return 1


def stop_worker():
    section("Stopping Celery worker")
    pid = read_pid()
    if pid and pid_alive(pid):
        kill_pid(pid)
        ok(f"Killed Celery PID={pid}")
    else:
        info("No Celery process found in PID file")
    PID_FILE.unlink(missing_ok=True)
    return 0


def show_status():
    section("Celery worker status")
    pid = read_pid()
    if pid and pid_alive(pid):
        ok(f"Celery RUNNING (PID={pid})")
        info(f"Logs: {LOG_FILE}")
    else:
        info("Celery NOT running")
        PID_FILE.unlink(missing_ok=True)
    # Redis
    if test_port(REDIS_HOST, REDIS_PORT):
        ok(f"Redis   {REDIS_HOST}:{REDIS_PORT}  RUNNING")
    else:
        info(f"Redis   {REDIS_HOST}:{REDIS_PORT}  NOT running")
    return 0


def section(msg: str):
    print()
    print(f"{C.CYAN}{'='*60}{C.RESET}")
    print(f"{C.CYAN}  {msg}{C.RESET}")
    print(f"{C.CYAN}{'='*60}{C.RESET}")


# ============================================================
#  main
# ============================================================
def main():
    if "--stop" in sys.argv:
        return stop_worker()
    if "--status" in sys.argv:
        return show_status()
    if "--detach" in sys.argv:
        return start_detach()
    return start_foreground()


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        err("Interrupted")
        rc = 130
    raise SystemExit(rc)
