"""
Verify Exit: 阶段退出标准验证
============================
功能: 单独运行 exit_checks, 不推进阶段
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_runner import StageRunner, ensure_dirs, load_config, run_command


def main():
    ensure_dirs()
    config = load_config()
    runner = StageRunner(config)

    parser = argparse.ArgumentParser(description="Verify Exit: 验证阶段退出标准")
    parser.add_argument("--stage", "-s", type=str, help="指定阶段 (默认当前)")
    args = parser.parse_args()

    stage_id = args.stage or config["current_stage"]
    if stage_id not in config["stages"]:
        print(f"❌ 阶段 {stage_id} 不存在")
        sys.exit(1)

    stage = config["stages"][stage_id]
    print(f"🔍 验证 [{stage_id}] {stage['name']} 退出标准\n")

    passed = runner._run_exit_checks(stage)
    if passed:
        print(f"\n✅ 全部退出标准通过")
        sys.exit(0)
    else:
        print(f"\n❌ 部分退出标准未通过")
        sys.exit(1)


if __name__ == "__main__":
    main()
