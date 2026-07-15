"""
CLI 启动参数解析（与 run.py / app.main.run / python -m app 共用）
================================================================
单一参数来源，避免脚本之间行为漂移。
"""
import argparse
import sys
from pathlib import Path


def _force_utf8_stdout():
    """Windows 默认 GBK 会让 uvicorn banner 乱码，统一改为 UTF-8."""
    if sys.platform == "win32":
        import io
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="image-annotation-backend",
        description="图像自动标注系统 - 后端服务",
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="监听地址 (默认 127.0.0.1；Docker/远程请用 0.0.0.0)")
    p.add_argument("--port", type=int, default=5000,
                   help="监听端口 (默认 5000)")
    p.add_argument("--reload", action="store_true",
                   help="代码变更自动重载（仅开发）")
    p.add_argument("--no-reload", dest="reload", action="store_false",
                   help="禁用自动重载（默认）")
    p.set_defaults(reload=False)
    p.add_argument("--workers", type=int, default=1,
                   help="worker 进程数 (默认 1；reload 模式下强制为 1)")
    p.add_argument("--log-level", default="info",
                   choices=["critical", "error", "warning", "info", "debug", "trace"],
                   help="uvicorn 日志级别 (默认 info)")
    p.add_argument("--no-banner", action="store_true",
                   help="不打印启动 banner")
    p.add_argument("--check", action="store_true",
                   help="只做环境/依赖检查，不真正启动 uvicorn")
    return p


def print_banner(host: str, port: int, reload: bool, workers: int, log_level: str):
    mode = "development (auto-reload)" if reload else "production"
    bar = "=" * 60
    print(bar, flush=True)
    print("  图像自动标注系统 - 后端服务", flush=True)
    print(bar, flush=True)
    print(f"  API       : http://{host}:{port}/", flush=True)
    print(f"  Swagger   : http://{host}:{port}/docs", flush=True)
    print(f"  ReDoc     : http://{host}:{port}/redoc", flush=True)
    print(f"  OpenAPI   : http://{host}:{port}/openapi.json", flush=True)
    print(f"  Health    : http://{host}:{port}/api/health", flush=True)
    print(f"  Mode      : {mode}", flush=True)
    print(f"  Workers   : {workers}", flush=True)
    print(f"  Log level : {log_level}", flush=True)
    print(bar, flush=True)


def ensure_backend_on_path() -> Path:
    """保证 `import app.*` 在任意 cwd 下都能找到"""
    here = Path(__file__).resolve().parent
    root = here.parent
    sp = str(root)
    if sp not in sys.path:
        sys.path.insert(0, sp)
    return root


def env_check() -> int:
    """轻量环境检查：Python/关键包/.env/DB可达性"""
    _force_utf8_stdout()
    print("=" * 60)
    print("  Environment check")
    print("=" * 60)
    # 1) Python
    print(f"  Python    : {sys.version.split()[0]}  ({sys.executable})")
    # 2) 关键依赖
    missing = []
    for pkg in ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "pydantic_settings",
                "redis", "celery", "minio", "loguru"):
        try:
            __import__(pkg)
            print(f"  {pkg:22s}: OK")
        except Exception as e:
            print(f"  {pkg:22s}: MISSING ({e})")
            missing.append(pkg)
    if missing:
        print(f"\n  [FAIL] missing packages: {missing}")
        return 1
    # 3) .env
    root = ensure_backend_on_path()
    env = root / ".env"
    print(f"  .env      : {'OK' if env.exists() else 'MISSING (using defaults)'}")
    # 4) Config import
    try:
        from app.config import settings  # noqa: F401
        print(f"  Config    : APP_ENV={settings.APP_ENV}  DEBUG={settings.APP_DEBUG}")
    except Exception as e:
        print(f"  Config    : FAIL ({e})")
        return 1
    print("\n  [OK] environment ready")
    return 0


def serve(args) -> int:
    """实际调用 uvicorn 启动服务"""
    _force_utf8_stdout()
    ensure_backend_on_path()

    if not args.no_banner:
        print_banner(args.host, args.port, args.reload, args.workers, args.log_level)

    # reload 模式下必须 workers=1
    workers = 1 if args.reload else max(1, args.workers)

    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=workers,
        log_level=args.log_level,
        access_log=True,
    )
    return 0


def main(argv=None) -> int:
    """统一入口：run.py / app.main.run / python -m app 都调用此函数"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check:
        return env_check()
    return serve(args)


if __name__ == "__main__":
    sys.exit(main())
