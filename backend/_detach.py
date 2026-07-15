"""
Helper: Detach a child process on Windows
Usage: python _detach.py <log_dir> <pid_file> <exe> [args...]

Creates a new process with DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
so the process survives the parent shell exiting.

Writes the actual child PID (not the parent) to <pid_file>, and
writes the listening port (if specified via --port) to <pid_file>.port
so the start script can verify the process is actually serving.
"""
import os
import sys
import subprocess
import time
from pathlib import Path

# Windows flags
# DETACHED_PROCESS (0x8): no console
# CREATE_NEW_PROCESS_GROUP (0x200): new process group, so Ctrl+C only affects this group
# CREATE_BREAKAWAY_FROM_JOB (0x1000000): if parent is in a job, child can leave
DETACHED = 0x8 | 0x200


def main():
    if len(sys.argv) < 4:
        print("Usage: _detach.py <log_dir> <pid_file> <exe> [args...]", file=sys.stderr)
        sys.exit(2)
    log_dir = Path(sys.argv[1])
    pid_file = Path(sys.argv[2])
    exe = sys.argv[3]
    args = sys.argv[4:]

    log_dir.mkdir(parents=True, exist_ok=True)
    out_log = log_dir / "backend.out.log"
    err_log = log_dir / "backend.err.log"

    f_out = open(out_log, "ab", buffering=0)
    f_err = open(err_log, "ab", buffering=0)

    p = subprocess.Popen(
        [exe] + args,
        stdout=f_out,
        stderr=f_err,
        stdin=subprocess.DEVNULL,
        creationflags=DETACHED,
        close_fds=True,
    )
    pid_file.write_text(str(p.pid), encoding="ascii")
    # Also try to capture listening port from args (e.g. --port 5000)
    listening_port = None
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            try:
                listening_port = int(args[i + 1])
            except ValueError:
                pass
    if listening_port:
        (pid_file.with_suffix(".port")).write_text(str(listening_port), encoding="ascii")
    print(f"Detached PID={p.pid}")
    print(f"Log: {out_log}")
    if listening_port:
        print(f"Port: {listening_port}")


if __name__ == "__main__":
    main()
