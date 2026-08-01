"""
Start FastAPI Backend API
=========================
启动后端 API 服务（uvicorn）

用法（从 backend/ 或项目根目录都行）：
  cd backend
  uv run python start_api.py                       # 前台模式 (Ctrl+C 退出)
  uv run python start_api.py --detach              # 后台 detached 模式 (父进程退出不影响)
  uv run python start_api.py --reload              # 改代码自动重载 (前台模式才有效)
  uv run python start_api.py --port 8000           # 改端口
  uv run python start_api.py --host 0.0.0.0        # 监听所有网卡
  uv run python start_api.py --stop                # 停 detached 模式
  uv run python start_api.py --status              # 查 detached 状态

停止：
  Ctrl+C  (前台模式)
  uv run python start_api.py --stop  (detached 模式)
"""
import os
import sys
import time
import socket
import subprocess
from pathlib import Path

# 切到 backend/ 目录（让 .env / pyproject.toml / app/ 都在正确位置）
BACKEND_DIR = Path(__file__).resolve().parent
LOGS_DIR = BACKEND_DIR.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
PID_FILE = LOGS_DIR / "backend.pid"
LOG_FILE = LOGS_DIR / "backend.out.log"
ERR_FILE = LOGS_DIR / "backend.err.log"

os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

import uvicorn  # noqa: E402

# 读 .env 配置（必须先 import settings，让 pydantic-settings 加载 .env）
from app.core.config import settings  # noqa: E402,E501


# ============================================================
#  依赖健康预检 (v3.1.0 Phase V #3)
# ============================================================
def _check_port(host: str, port: int, timeout: float = 0.5) -> bool:
    """TCP 端口探活."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def preflight() -> list[str]:
    """启 API 前检查依赖. 返回 warning 列表 (空 = 全 OK, 不阻止启动).

    Phase V #3: 启服务前先 sanity-check MySQL/Redis/MinIO, 让启动失败更明显.
    之前用户启 start_api -> 启动看着像好 -> /api/health 才发现 Redis 不可用,
    浪费时间排查. 现在 status 行给出明确诊断.

    v3.3.0: STORAGE_BACKEND=local 时跳过 MinIO 探测 (避免误报 + 节省资源).
    """
    warnings: list[str] = []
    deps = [
        ("MySQL", settings.MYSQL_HOST, settings.MYSQL_PORT, True),
        ("Redis", settings.REDIS_HOST, settings.REDIS_PORT, True),
    ]
    # v3.3.0: 仅当 STORAGE_BACKEND=minio 时探测 MinIO (local 后端无 MinIO 进程)
    is_minio_backend = (settings.STORAGE_BACKEND or "local").lower() == "minio"
    if is_minio_backend:
        try:
            mhost = settings.MINIO_ENDPOINT.split(":")[0]
            mport = int(settings.MINIO_ENDPOINT.split(":")[1])
            deps.append(("MinIO", mhost, mport, False))
        except Exception:
            warnings.append(f"  ⚠  MinIO endpoint 解析失败: {settings.MINIO_ENDPOINT!r}")

    for name, host, port, required in deps:
        if _check_port(host, port):
            print(f"  [OK ] {name:7s} {host}:{port}  reachable")
        else:
            msg = f"  [WARN] {name:7s} {host}:{port}  NOT reachable"
            if required:
                msg += "  (REQUIRED — API may fail on DB/cache calls)"
            warnings.append(msg)
            print(msg)

    # Admin SECRET_KEY 弱密码警告 (production)
    if settings.APP_ENV == "production":
        weak = settings.SECRET_KEY in ("change-me-to-a-random-string-min-32-chars", "secret", "")
        if weak:
            warnings.append("  [ERR ] SECRET_KEY is default/weak; required for production")
            print("  [ERR ] SECRET_KEY is default/weak; required for production")
    return warnings


def parse_args():
    reload = "--reload" in sys.argv
    detach = "--detach" in sys.argv
    port = settings.APP_PORT
    host = settings.APP_HOST
    workers = 1
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        if i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    if "--host" in sys.argv:
        i = sys.argv.index("--host")
        if i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
    if "--workers" in sys.argv:
        i = sys.argv.index("--workers")
        if i + 1 < len(sys.argv):
            workers = int(sys.argv[i + 1])
    return host, port, reload, workers, detach


def test_port(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def wait_port(host: str, port: int, timeout: float = 30.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if test_port(host, port, 0.3):
            return True
        time.sleep(0.5)
    return False


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


def print_banner(host, port, reload, workers, detach):
    print("=" * 60)
    print("  图像自动标注系统 - 后端 API 服务")
    print("=" * 60)
    print(f"  API      : http://{host}:{port}/")
    print(f"  Swagger  : http://{host}:{port}/docs")
    print(f"  ReDoc    : http://{host}:{port}/redoc")
    print(f"  OpenAPI  : http://{host}:{port}/openapi.json")
    print(f"  Health   : http://{host}:{port}/api/health")
    print(f"  Env      : {settings.APP_ENV}")
    print(f"  Mode     : {'development (auto-reload)' if reload else 'production'}")
    print(f"  Workers  : {workers}")
    print(f"  Detach   : {detach}")
    print(f"  MySQL    : {settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}")
    print(f"  Redis    : {settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}")
    print(f"  MinIO    : {settings.MINIO_ENDPOINT}")
    print("=" * 60)
    if detach:
        print(f"  PID      : {PID_FILE}")
        print(f"  Logs     : {LOG_FILE}  |  {ERR_FILE}")
        print("  Stop     : uv run python start_api.py --stop")
    else:
        print("  按 Ctrl+C 停止服务")
    print("=" * 60)


# ============================================================
#  Start modes
# ============================================================
def start_foreground():
    """前台模式：直接调 uvicorn.run()"""
    host, port, reload, workers, _ = parse_args()
    print_banner(host, port, reload, workers, detach=False)

    # v3.1.0 Phase V #3: 启动前依赖健康预检 (改进 start_api 失败诊断体验)
    print()
    print("  [Preflight] Checking required services...")
    preflight()

    if reload and workers > 1:
        print("[WARN] --reload 不兼容多 workers，自动降为 1 worker")
        workers = 1

    uvicorn.run(
        "app.main:app",
        host=host, port=port,
        reload=reload, workers=workers,
        log_level="info",
    )


def start_detach():
    """detached 模式：subprocess.Popen + DETACHED_PROCESS（父进程退出不影响）"""
    host, port, reload, workers, _ = parse_args()
    print_banner(host, port, reload, workers, detach=True)

    # v3.1.0 Phase V #3: 启动前依赖健康预检
    print()
    print("  [Preflight] Checking required services...")
    preflight()

    # Check if already running
    existing = read_pid()
    if existing and pid_alive(existing) and test_port(host, port):
        print(f"  [OK] API 已在跑 (PID={existing})，无需重复启")
        return 0
    PID_FILE.unlink(missing_ok=True)

    # 检查端口占用
    if test_port(host, port):
        print(f"  [WARN] 端口 {port} 已被占用（可能是其它进程），不再启动")
        return 1

    # 构造启动参数（用 Popen 重启自己，detached）
    argv = [
        sys.executable,  # uv run python 的解释器
        str(Path(__file__).resolve()),
        "--host", host, "--port", str(port),
    ]
    if workers > 1:
        argv += ["--workers", str(workers)]
    if reload:
        argv += ["--reload"]

    log = open(LOG_FILE, "ab", buffering=0)
    err = open(ERR_FILE, "ab", buffering=0)
    p = subprocess.Popen(
        argv,
        cwd=str(BACKEND_DIR),
        stdout=log, stderr=err, stdin=subprocess.DEVNULL,
        creationflags=0x00000008,  # DETACHED_PROCESS
        close_fds=True,
    )
    log.close()
    err.close()
    write_pid(p.pid)
    print(f"  [OK] Launcher PID={p.pid}")

    # Wait port
    if not wait_port(host, port, timeout=30):
        print(f"  [ERR] 端口 {port} 30s 内未监听，检查 {ERR_FILE}")
        return 1
    print(f"  [OK] API listening on :{port}")
    return 0


def stop_api():
    """停止 detached 模式的 API"""
    print("=" * 60)
    print("  Stop backend API")
    print("=" * 60)
    pid = read_pid()
    if pid and pid_alive(pid):
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
            print(f"  [OK] Killed API PID={pid}")
        except Exception as e:
            print(f"  [ERR] Kill failed: {e}")
    else:
        print("  [INFO] No API process found in PID file")
    PID_FILE.unlink(missing_ok=True)
    return 0


def show_status():
    print("=" * 60)
    print("  Backend API status")
    print("=" * 60)
    pid = read_pid()
    if pid and pid_alive(pid):
        print(f"  [OK] RUNNING  PID={pid}")
    else:
        print("  [INFO] NOT running")
    # 端口探活 (v3.1.0 Phase V #3: 优先查 settings.APP_PORT, 兼容 5000 也探)
    print("\n  Listening ports:")
    for port in (settings.APP_PORT, 5000):
        if test_port("127.0.0.1", port):
            print(f"  [OK]   :{port}  LISTENING")
        else:
            print(f"  [INFO] :{port}  not listening")
    # /api/health 探活
    print()
    health_port = settings.APP_PORT if test_port("127.0.0.1", settings.APP_PORT) else 5000
    try:
        import urllib.request, json
        r = urllib.request.urlopen(f"http://127.0.0.1:{health_port}/api/health", timeout=3)
        d = json.loads(r.read().decode())
        print(f"  [OK] /api/health (port {health_port}): status={d.get('status')}")
    except Exception as e:
        print(f"  [INFO] /api/health: {e}")
    return 0


# ============================================================
#  main
# ============================================================
def main():
    if "--stop" in sys.argv:
        return stop_api()
    if "--status" in sys.argv:
        return show_status()
    if "--detach" in sys.argv:
        return start_detach()
    return start_foreground()


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        print("\n[INFO] API 服务已停止")
        rc = 0
    raise SystemExit(rc or 0)
