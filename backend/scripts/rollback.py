"""
Rollback: 阶段回滚
==================
功能: 取消当前阶段任务标记 + git revert + 配置回滚
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_runner import (
    DOCS_DIR, StageRunner, ensure_dirs, load_config, save_config,
    unmark_task,
)


def main():
    ensure_dirs()
    config = load_config()
    runner = StageRunner(config)

    parser = argparse.ArgumentParser(description="Rollback: 阶段回滚")
    parser.add_argument("--stages", "-n", type=int, default=1, help="回滚几个阶段")
    parser.add_argument("--no-git", action="store_true", help="不操作 git")
    args = parser.parse_args()

    # 1. git revert
    if not args.no_git:
        print(f"⏪ git revert HEAD~{args.stages}..HEAD")
        import subprocess
        result = subprocess.run(
            ["git", "revert", "--no-commit", f"HEAD~{args.stages}..HEAD"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"⚠️ git revert 失败: {result.stderr[:200]}")
            print(f"   可能没有足够 commits, 跳过 git 操作")
        else:
            print(f"✅ git revert 成功 (未 commit, 待人工确认)")

    # 2. 取消当前阶段所有任务标记
    current = config["current_stage"]
    print(f"\n🗑️ 取消 [{current}] 所有任务标记")
    for t in config["stages"][current]["tasks"]:
        unmark_task(t["id"])

    # 3. 配置回滚
    prev = runner._prev_stage()
    if prev:
        config["current_stage"] = prev
        save_config(config)
        print(f"\n✅ 配置回滚到 [{prev}] {config['stages'][prev]['name']}")
        # 更新看板
        runner._update_progress_board()
        print(f"📊 看板已更新")
    else:
        print(f"\n⚠️ 已在第一阶段, 无前置阶段可回滚")


if __name__ == "__main__":
    main()
