"""
Task Done: 任务完成标记工具
===========================
功能: 单独运行的任务完成标记工具
      (stage_runner.py --mark-complete 的独立入口)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许直接运行
sys.path.insert(0, str(Path(__file__).parent))
from stage_runner import StageRunner, ensure_dirs, load_config, save_config


def main():
    ensure_dirs()
    parser = argparse.ArgumentParser(
        description="Task Done: 标记/取消任务完成",
    )
    parser.add_argument("task_id", help="任务 ID, 如 S1-T1")
    parser.add_argument("--note", "-n", default="", help="完成备注")
    parser.add_argument("--unmark", "-u", action="store_true", help="取消完成标记")
    args = parser.parse_args()

    config = load_config()
    runner = StageRunner(config)

    if args.unmark:
        from stage_runner import unmark_task
        unmark_task(args.task_id)
        runner._update_progress_board()
        print(f"📊 看板已更新")
    else:
        runner.mark_complete(args.task_id, args.note)


if __name__ == "__main__":
    main()
