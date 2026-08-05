"""
性能基准测试执行脚本 (v3.3.1 L5)
=================================

自动化运行 locust 性能测试并生成报告.

用法:
  python tests/load/run_benchmark.py --users 50 --spawn 10 --duration 60

要求:
  - locust 已安装 (pip install locust)
  - 后端服务运行在 http://localhost:8000
  - 已存在测试用户 (perf_admin / perf_user)
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def run_benchmark(host: str, users: int, spawn_rate: int, duration: int,
                  output_dir: str = "perf-results"):
    """执行 locust 性能测试."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    csv_prefix = os.path.join(output_dir, f"perf-{timestamp}")

    # 构造 locust 命令
    locust_file = Path(__file__).parent / "locust_team.py"
    cmd = [
        "locust",
        "-f", str(locust_file),
        "--host", host,
        "--headless",
        "-u", str(users),
        "-r", str(spawn_rate),
        "-t", f"{duration}s",
        "--csv", csv_prefix,
        "--csv-full-history",
        "--html", f"{csv_prefix}.html",
    ]
    print(f"[Run] {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=False, timeout=duration + 30)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("[WARN] locust 超时, 强制结束")
        return -1


def main():
    parser = argparse.ArgumentParser(description="团队管理性能基准测试")
    parser.add_argument("--host", default="http://localhost:8000", help="API host")
    parser.add_argument("--users", type=int, default=50, help="并发用户数")
    parser.add_argument("--spawn", type=int, default=10, help="启动速率")
    parser.add_argument("--duration", type=int, default=60, help="持续时间(秒)")
    parser.add_argument("--output", default="perf-results", help="输出目录")
    args = parser.parse_args()

    print(f"配置: host={args.host} users={args.users} spawn={args.spawn} duration={args.duration}s")
    rc = run_benchmark(args.host, args.users, args.spawn, args.duration, args.output)
    sys.exit(rc)


if __name__ == "__main__":
    main()
