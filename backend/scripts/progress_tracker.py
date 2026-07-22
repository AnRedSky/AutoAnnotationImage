"""
Progress Tracker: 进度看板自动更新
==================================
功能: 单独运行的进度看板更新工具
      (stage_runner.py 内置此功能, 此脚本是独立入口)
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行
sys.path.insert(0, str(Path(__file__).parent))
from stage_runner import (
    PROGRESS_FILE, StageRunner, ensure_dirs, load_config, save_config,
)


def main():
    ensure_dirs()
    config = load_config()
    runner = StageRunner(config)
    runner._update_progress_board()
    print(f"📊 进度看板已更新: {PROGRESS_FILE}")


if __name__ == "__main__":
    main()
