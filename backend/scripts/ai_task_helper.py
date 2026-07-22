"""
AI Task Helper: AI 协助任务执行
================================
功能: 对 AI 友好地提供任务上下文, 便于 AI 协助完成代码改动
      支持:
        1. 输出单任务上下文 (供 AI 读取)
        2. 应用 AI 生成的代码 diff (需人工确认)
        3. 记录 AI 协助历史
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_runner import (
    DOCS_DIR, LOGS_DIR, PROJECT_ROOT, StageRunner, ensure_dirs,
    is_task_done, load_config,
)

AI_LOG_FILE = LOGS_DIR / "ai_assist_history.jsonl"


def render_task_context(task_id: str) -> str:
    """渲染任务上下文 (供 AI 读取)"""
    config = load_config()
    runner = StageRunner(config)

    # 查找任务
    target_task = None
    target_stage = None
    for stage_id, stage in config["stages"].items():
        for t in stage["tasks"]:
            if t["id"] == task_id:
                target_task = t
                target_stage = (stage_id, stage)
                break

    if not target_task:
        return f"❌ 任务 {task_id} 不存在"

    stage_id, stage = target_stage

    # 读取相关文件内容
    files_content = {}
    for f in target_task.get("files", []):
        path = PROJECT_ROOT / f
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            # 截取关键段落 (前后 50 行, 含任务关键词)
            lines = content.splitlines()
            # 简单实现: 全文 (实际可优化为相关段落)
            files_content[f] = content[:5000]  # 限 5KB

    # 构造上下文
    context = f"""# AI 任务上下文: {task_id}

## 任务元信息
- 任务 ID: {target_task['id']}
- 标题: {target_task['title']}
- 描述: {target_task.get('description', '(无)')}
- 涉及文件: {target_task.get('files', [])}
- 预计耗时: {target_task.get('estimated_hours', '?')}h
- 是否 AI 协助: {target_task.get('ai_assisted', False)}

## 所属阶段
- 阶段 ID: {stage_id}
- 阶段名称: {stage['name']}
- 阶段目标: {stage['goal']}

## 退出标准
{json.dumps(stage.get('exit_checks', []), ensure_ascii=False, indent=2)}

## 相关代码片段
"""
    for f, content in files_content.items():
        context += f"\n### `{f}`\n\n```\n{content}\n```\n"

    context += f"""

## 当前任务状态
- 是否完成: {'✅ 是' if is_task_done(task_id) else '⬜ 否'}

## 期望输出
请生成代码修改建议, 格式:

```diff
--- a/{target_task.get('files', ['?'])[0]}
+++ b/{target_task.get('files', ['?'])[0]}
@@ -X,Y +X,Y @@
 context
+added line
-removed line
```

或者自然语言说明修改步骤。
"""
    return context


def log_ai_action(task_id: str, action: str, content: str):
    """记录 AI 协助历史"""
    AI_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now().isoformat(),
        "task_id": task_id,
        "action": action,
        "content": content[:2000],  # 截断
    }
    with open(AI_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    ensure_dirs()
    parser = argparse.ArgumentParser(
        description="AI Task Helper: AI 协助任务执行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ai_task_helper.py context S1-T1     # 输出 S1-T1 上下文
  python ai_task_helper.py log S1-T1 done     # 记录 S1-T1 已完成
        """,
    )
    parser.add_argument("action", choices=["context", "log", "list"], help="操作")
    parser.add_argument("task_id", nargs="?", help="任务 ID")
    parser.add_argument("status", nargs="?", help="状态 (用于 log)")

    args = parser.parse_args()

    if args.action == "context":
        if not args.task_id:
            print("❌ 需要 task_id")
            sys.exit(1)
        print(render_task_context(args.task_id))
    elif args.action == "log":
        if not args.task_id or not args.status:
            print("❌ 需要 task_id 和 status")
            sys.exit(1)
        log_ai_action(args.task_id, args.status, "AI 协助记录")
        print(f"📝 已记录: {args.task_id} = {args.status}")
    elif args.action == "list":
        if AI_LOG_FILE.exists():
            print(AI_LOG_FILE.read_text(encoding="utf-8"))
        else:
            print("(无历史)")


if __name__ == "__main__":
    main()
