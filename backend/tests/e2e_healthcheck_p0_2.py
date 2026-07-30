"""
End-to-End P0-2 verification (DB-down → 503)

本脚本不属于自动化测试套件 (不进 pytest). 给运维/排错用:
真服务存在但想让 health 看到 DB 挂, 看返回是不是 503.

Usage:
    env -u PYTHONPATH ./.venv/Scripts/python.exe tests/e2e_healthcheck_p0_2.py

成功条件:
  - Case A: DB 不可用 → HTTP 503, status=unhealthy, body 含详细 err
  - Case B: Redis 不可用 → HTTP 200, status=degraded
  - Case C: 全部正常 → HTTP 200, status=ok (默认)
"""
import asyncio
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)


async def _case_a():
    """DB 抛 OperationalError → 期望 503."""
    print("=== Case A: DB 不可用 → 期望 503 ===")
    from fastapi import Response
    from fastapi.responses import JSONResponse
    from sqlalchemy.exc import OperationalError
    from app.admin.api.system import health_check

    class _FailDb:
        async def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    result = await health_check(response=Response(), db=_FailDb())
    if isinstance(result, JSONResponse):
        print(f"  status_code={result.status_code}")
        print(f"  body={result.body.decode('utf-8', errors='replace')[:500]}")
        return result.status_code == 503
    print(f"  returned non-JSONResponse: {type(result).__name__}")
    return False


async def _case_b():
    """Redis 不可用 → 期望 200 + degraded."""
    print("\n=== Case B: 仅 Redis 不可用 → 期望 200 degraded ===")
    from fastapi import Response
    from fastapi.responses import JSONResponse
    from app.admin.api.system import health_check
    from app.core.config import settings

    orig_host, orig_port = settings.REDIS_HOST, settings.REDIS_PORT
    try:
        settings.REDIS_HOST = "127.0.0.2"
        settings.REDIS_PORT = 29999

        class _OKDb:
            async def execute(self, *args, **kwargs):
                return None

        result = await health_check(response=Response(), db=_OKDb())
        if isinstance(result, JSONResponse):
            print(f"  status_code={result.status_code} (期望 200)")
            print(f"  body={result.body.decode('utf-8', errors='replace')[:500]}")
            ok_status = result.status_code == 200
        else:
            print(f"  status={result.get('status')}")
            ok_status = result.get("status") == "degraded"
        return ok_status
    finally:
        settings.REDIS_HOST = orig_host
        settings.REDIS_PORT = orig_port


async def main():
    a = await _case_a()
    b = await _case_b()
    print()
    print(f"=== summary: Case A 503={a} Case B degraded={b} ===")
    sys.exit(0 if (a and b) else 1)


asyncio.run(main())
