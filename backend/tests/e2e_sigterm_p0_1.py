"""
End-to-End P0-1 verification: Worker SIGTERM 真写到 Redis emergency marker.

本脚本不属于自动化测试套件 (不进 pytest, 因为本地不用真发 SIGTERM).
给运维/排错/部署前 sanity-check 用.

Usage:
    env -u PYTHONPATH ./.venv/Scripts/python.exe tests/e2e_sigterm_p0_1.py

成功条件:
  - redis 中存在 key ``train_emergency:<TASK_ID>`` 且 value 含 "sigterm"
"""
import os
import signal
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import redis as redis_sync
from app.tasks.workers.celery_app import celery_app
from app.tasks.workers.signal_handlers import (
    install_sigterm_handler,
    set_current_task_id,
)

TASK_ID = f"e2e-sigterm-{int(time.time())}"


def main():
    print(f"[setup] TASK_ID = {TASK_ID}")
    install_sigterm_handler(celery_app)
    set_current_task_id(TASK_ID)

    r = redis_sync.Redis(host="127.0.0.1", port=9770, db=0, decode_responses=True)
    r.delete(f"train_emergency:{TASK_ID}")

    print(f"[action] sending SIGTERM to PID {os.getpid()}")
    try:
        signal.raise_signal(signal.SIGTERM)
    except AttributeError:
        os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(0.5)

    val = r.get(f"train_emergency:{TASK_ID}")
    ttl = r.ttl(f"train_emergency:{TASK_ID}")
    print(f"[verify] train_emergency:{TASK_ID}")
    print(f"   val = {val!r}")
    print(f"   ttl = {ttl}s")

    ok = val is not None and "sigterm" in val
    if not ok:
        print("[FAIL] SIGTERM handler 没写入 Redis emergency marker")
        sys.exit(1)

    print("[OK] SIGTERM handler 真把 emergency marker 写到了 Redis")

    # cleanup
    r.delete(f"train_emergency:{TASK_ID}")
    print(f"[cleanup] removed train_emergency:{TASK_ID}")


if __name__ == "__main__":
    main()
