"""
Stage Runner: 阶段编排器
========================
功能:
  1. 读取 stage_config.yaml 当前阶段配置
  2. 执行阶段任务 (支持 AI 协助 + 人工标记)
  3. 验证完成度 (跑 exit_checks)
  4. 自动生成阶段报告 S{N}_REPORT.md
  5. 自动 commit + 推进到下一阶段

使用:
  python stage_runner.py                       # 执行当前阶段
  python stage_runner.py --status              # 查看进度
  python stage_runner.py --stage S1            # 指定阶段
  python stage_runner.py --mark-complete S1-T1 # 标记任务完成
  python stage_runner.py --jump S2             # 跳到指定阶段
  python stage_runner.py --rollback            # 回滚到上一阶段
  python stage_runner.py --force-next          # 强制推进 (跳过阈值)

设计原则:
  - 单人 + AI 协作, 不阻塞 AI 调用
  - 完成度阈值 (默认 100%) 触发自动流转
  - 任务完成 = 标记文件存在 (.task_done/{task_id}.done)
  - 阶段完成 = 全部任务完成 + exit_checks 通过
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("缺少 pyyaml 依赖, 请先: pip install pyyaml")
    sys.exit(1)


# =============================================================================
# 路径常量
# =============================================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = PROJECT_ROOT / "backend" / "docs"
TASK_DONE_DIR = DOCS_DIR / ".task_done"
LOGS_DIR = DOCS_DIR / "stage_logs"
CONFIG_FILE = DOCS_DIR / "stage_config.yaml"
PROGRESS_FILE = DOCS_DIR / "PROGRESS.md"


# =============================================================================
# 工具函数
# =============================================================================
def ensure_dirs():
    """确保目录存在"""
    for d in [TASK_DONE_DIR, LOGS_DIR, DOCS_DIR / "templates"]:
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    """读取 stage_config.yaml"""
    if not CONFIG_FILE.exists():
        print(f"❌ 配置文件不存在: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: Dict[str, Any]):
    """写回 stage_config.yaml"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def is_task_done(task_id: str) -> bool:
    """任务是否完成 (基于标记文件)"""
    return (TASK_DONE_DIR / f"{task_id}.done").exists()


def mark_task_done(task_id: str, note: str = ""):
    """标记任务完成"""
    marker = TASK_DONE_DIR / f"{task_id}.done"
    content = f"task_id: {task_id}\ncompleted_at: {datetime.now().isoformat()}\nnote: {note}\n"
    marker.write_text(content, encoding="utf-8")
    print(f"  ✅ 任务 {task_id} 已标记完成")


def unmark_task(task_id: str):
    """取消任务完成标记"""
    marker = TASK_DONE_DIR / f"{task_id}.done"
    if marker.exists():
        marker.unlink()
        print(f"  🔄 任务 {task_id} 已取消标记")


def run_command(cmd: str, cwd: Optional[Path] = None) -> Dict[str, Any]:
    """执行 shell 命令"""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd or PROJECT_ROOT,
            capture_output=True, text=True, timeout=300,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout (300s)", "returncode": -1}
    except Exception as e:
        return {"ok": False, "error": str(e), "returncode": -1}


def git_commit(message: str, files: List[str]) -> Optional[str]:
    """git add + commit, 返回 commit hash"""
    try:
        for f in files:
            if Path(f).exists():
                subprocess.run(["git", "add", f], cwd=PROJECT_ROOT, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
        )
        # 解析 commit hash
        hash_result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, check=True,
        )
        commit_hash = hash_result.stdout.strip()[:8]
        print(f"  📝 已 commit: {commit_hash} {message}")
        return commit_hash
    except subprocess.CalledProcessError as e:
        print(f"  ⚠️ git commit 失败: {e.stderr[:200]}")
        return None


# =============================================================================
# 编排器核心
# =============================================================================
class StageRunner:
    """阶段编排器 - 单人 + AI 协作"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.stages: Dict[str, Any] = config["stages"]
        self.current_stage_id: str = config["current_stage"]
        self.stage_order: List[str] = list(self.stages.keys())

    # ---------------------------------------------------------------------
    # 状态查询
    # ---------------------------------------------------------------------
    def status(self):
        """打印整体进度"""
        print("=" * 70)
        print(f"📊 项目进度看板 - {self.config['project']} → {self.config['version_target']}")
        print("=" * 70)

        total_tasks = 0
        completed_tasks = 0
        for stage_id, stage in self.stages.items():
            tasks = stage["tasks"]
            done = sum(1 for t in tasks if is_task_done(t["id"]))
            total = len(tasks)
            total_tasks += total
            completed_tasks += done

            # 状态标识
            if done == total:
                icon = "✅"
            elif done == 0:
                icon = "⚪"
            else:
                icon = "🔄"

            current_marker = " ⬅️ 当前" if stage_id == self.current_stage_id else ""
            print(f"\n{icon} [{stage_id}] {stage['name']}{current_marker}")
            print(f"   目标: {stage['goal']}")
            print(f"   进度: {done}/{total} ({done/total*100:.0f}%)")
            print(f"   优先级: {stage['priority']}")

        print("\n" + "=" * 70)
        print(f"📈 整体完成度: {completed_tasks}/{total_tasks} ({completed_tasks/total_tasks*100:.1f}%)")
        print(f"🎯 当前阶段: {self.current_stage_id} - {self.stages[self.current_stage_id]['name']}")
        print("=" * 70)

    # ---------------------------------------------------------------------
    # 阶段执行
    # ---------------------------------------------------------------------
    def run_stage(self, stage_id: Optional[str] = None, force: bool = False):
        """执行指定阶段 (或当前阶段)"""
        stage_id = stage_id or self.current_stage_id
        if stage_id not in self.stages:
            print(f"❌ 阶段 {stage_id} 不存在")
            return

        stage = self.stages[stage_id]
        self.current_stage_id = stage_id
        self.config["current_stage"] = stage_id
        self.config["current_stage_status"] = "IN_PROGRESS"

        print("\n" + "=" * 70)
        print(f"🚀 启动阶段: [{stage_id}] {stage['name']}")
        print(f"   目标: {stage['goal']}")
        print(f"   优先级: {stage['priority']}")
        print("=" * 70 + "\n")

        # 1. 检查前置依赖
        deps = stage.get("depends_on", [])
        for dep in deps:
            dep_stage = self.stages[dep]
            dep_done = sum(1 for t in dep_stage["tasks"] if is_task_done(t["id"]))
            dep_total = len(dep_stage["tasks"])
            if dep_done < dep_total and not force:
                print(f"❌ 前置阶段 {dep} 未完成 ({dep_done}/{dep_total}), 无法启动 {stage_id}")
                print(f"   提示: 使用 --force 跳过依赖检查, 或先完成 {dep}")
                return

        # 2. 列出待完成任务
        pending_tasks = [t for t in stage["tasks"] if not is_task_done(t["id"])]
        if not pending_tasks:
            print(f"✅ 阶段 {stage_id} 全部任务已完成, 验证退出标准...")
        else:
            print(f"📋 待完成任务 ({len(pending_tasks)}/{len(stage['tasks'])}):\n")
            for t in pending_tasks:
                ai_tag = "🤖" if t.get("ai_assisted") else "👤"
                print(f"   {ai_tag} [{t['id']}] {t['title']}")
                print(f"      预计: {t.get('estimated_hours', '?')}h, 涉及: {', '.join(t.get('files', []))}")
            print(f"\n💡 标记完成: python stage_runner.py --mark-complete {pending_tasks[0]['id']}")
            print(f"   或单次执行: 完成一个任务后重新运行本脚本\n")

        # 3. 验证退出标准
        completion = self._compute_completion(stage)
        if completion["percent"] < stage["exit_threshold"] and not force:
            print(f"\n⏸️ 阶段 {stage_id} 完成度 {completion['percent']:.0f}% < 阈值 {stage['exit_threshold']}%")
            print(f"   剩余任务: {len(completion['pending_tasks'])} 个")
            return

        # 4. 跑 exit_checks
        if not self._run_exit_checks(stage):
            print(f"\n❌ 退出检查未通过, 请修复后重试")
            return

        # 5. 生成报告 + 推进
        if completion["percent"] >= stage["exit_threshold"]:
            self._finalize_stage(stage, completion)
        else:
            # force 模式直接推进
            if force:
                print(f"\n⚠️ --force 模式: 完成度 {completion['percent']:.0f}% 但强制推进")
                self._finalize_stage(stage, completion)
            else:
                print(f"\n⏸️ 完成度不足, 阶段保持 IN_PROGRESS")

    def _finalize_stage(self, stage: Dict, completion: Dict):
        """完成阶段: 生成报告 + commit + 推进下一阶段"""
        stage_id = stage["id"] if "id" in stage else f"S{list(self.stages.keys()).index(self.current_stage_id) + 1}"
        # 实际从 stage_order 取
        stage_id = self.current_stage_id

        # 1. 生成报告
        report_path = self._generate_report(stage, completion)
        print(f"\n📄 阶段报告已生成: {report_path.name}")

        # 2. 更新进度看板
        self._update_progress_board()
        print(f"📊 进度看板已更新: PROGRESS.md")

        # 3. git commit
        commit_hash = git_commit(
            f"docs(stage): {stage_id} {stage['name']} 阶段完成",
            [str(report_path), str(PROGRESS_FILE)],
        )

        # 4. 推进到下一阶段
        next_stage = self._next_stage()
        if next_stage:
            self.config["current_stage"] = next_stage
            self.config["current_stage_status"] = "PENDING"
            self.current_stage_id = next_stage
            save_config(self.config)
            print(f"\n✅ 阶段 {stage_id} 完成, 自动推进到 [{next_stage}] {self.stages[next_stage]['name']}")
            print(f"   下阶段目标: {self.stages[next_stage]['goal']}")
        else:
            self.config["current_stage_status"] = "ALL_COMPLETED"
            save_config(self.config)
            print(f"\n🎉 所有阶段已完成! 项目进入发布阶段")
            print(f"   下一步: git tag v{self.config['version_target']}")

    # ---------------------------------------------------------------------
    # 辅助方法
    # ---------------------------------------------------------------------
    def _compute_completion(self, stage: Dict) -> Dict:
        """计算完成度"""
        total = len(stage["tasks"])
        done = sum(1 for t in stage["tasks"] if is_task_done(t["id"]))
        return {
            "total": total,
            "completed": done,
            "percent": done / total * 100 if total > 0 else 0,
            "pending_tasks": [t["id"] for t in stage["tasks"] if not is_task_done(t["id"])],
        }

    def _run_exit_checks(self, stage: Dict) -> bool:
        """执行 exit_checks"""
        checks = stage.get("exit_checks", [])
        if not checks:
            return True

        print(f"\n🔍 执行退出检查 ({len(checks)} 项):")
        all_passed = True
        for check in checks:
            desc = check.get("description", "")
            cmd = check.get("command", "")
            print(f"   ▶ {desc}")
            print(f"     $ {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            result = run_command(cmd)
            if result["ok"]:
                print(f"     ✅ 通过")
            else:
                print(f"     ❌ 失败 (returncode={result.get('returncode', '?')})")
                if result.get("stderr"):
                    print(f"     stderr: {result['stderr'][:200]}")
                if result.get("stdout"):
                    print(f"     stdout: {result['stdout'][:200]}")
                all_passed = False
        return all_passed

    def _next_stage(self) -> Optional[str]:
        """获取下一阶段 ID"""
        try:
            idx = self.stage_order.index(self.current_stage_id)
            return self.stage_order[idx + 1] if idx + 1 < len(self.stage_order) else None
        except ValueError:
            return None

    def _prev_stage(self) -> Optional[str]:
        """获取上一阶段 ID"""
        try:
            idx = self.stage_order.index(self.current_stage_id)
            return self.stage_order[idx - 1] if idx > 0 else None
        except ValueError:
            return None

    def _generate_report(self, stage: Dict, completion: Dict) -> Path:
        """生成阶段报告"""
        stage_id = self.current_stage_id
        stage_num = stage_id[1:]  # S1 -> 1
        report_path = DOCS_DIR / f"S{stage_num}_REPORT.md"

        # 收集任务结果
        task_results = []
        for t in stage["tasks"]:
            marker = TASK_DONE_DIR / f"{t['id']}.done"
            if marker.exists():
                content = marker.read_text(encoding="utf-8")
                completed_at = ""
                note = ""
                for line in content.splitlines():
                    if line.startswith("completed_at:"):
                        completed_at = line.split(":", 1)[1].strip()
                    if line.startswith("note:"):
                        note = line.split(":", 1)[1].strip()
                task_results.append({
                    "task_id": t["id"],
                    "title": t["title"],
                    "status": "✅",
                    "completed_at": completed_at,
                    "note": note,
                })
            else:
                task_results.append({
                    "task_id": t["id"],
                    "title": t["title"],
                    "status": "⬜",
                    "completed_at": "-",
                    "note": "-",
                })

        # 渲染报告
        report = self._render_report(stage, completion, task_results)
        report_path.write_text(report, encoding="utf-8")
        return report_path

    def _render_report(self, stage: Dict, completion: Dict, task_results: List[Dict]) -> str:
        """渲染 Markdown 报告"""
        stage_id = self.current_stage_id

        # 任务表格
        task_table = "\n".join([
            f"| {tr['task_id']} | {tr['title']} | {tr['status']} | {tr['completed_at']} | {tr['note']} |"
            for tr in task_results
        ])

        # exit_checks 表格
        checks_table = "\n".join([
            f"| {c.get('description', '')} | {'✅' if run_command(c.get('command', ''), cwd=DOCS_DIR).get('ok') else '❌'} |"
            for c in stage.get("exit_checks", [])
        ]) or "| (无退出检查) | - |"

        return f"""# 阶段 {stage_id} 报告: {stage['name']}

> **阶段 ID**: {stage_id}
> **生成时间**: {datetime.now().isoformat()}
> **状态**: ✅ 完成
> **完成度**: {completion['percent']:.0f}% ({completion['completed']}/{completion['total']})

---

## 一、阶段目标

{stage['goal']}

**优先级**: {stage['priority']}

**依赖阶段**: {', '.join(stage.get('depends_on', ['(无)']))}

---

## 二、任务完成情况

| 任务 ID | 标题 | 状态 | 完成时间 | 备注 |
|---|---|---|---|---|
{task_table}

**汇总**: 完成 {completion['completed']} 个, 跳过 {completion['total'] - completion['completed']} 个

---

## 三、退出检查

| 检查项 | 结果 |
|---|---|
{checks_table}

---

## 四、关键产出

### 4.1 代码改动

待 `git log --oneline` 补充 (本报告生成后由编排器自动 commit)

### 4.2 新增/修改文件

待 `git diff --stat HEAD~1` 补充

### 4.3 阶段报告

- 本报告: `backend/docs/{stage_id.replace('S', 'S')}_REPORT.md`
- 进度看板: `backend/docs/PROGRESS.md`

---

## 五、下阶段准备

**下一阶段**: {self._next_stage() or '(无, 全部完成)'}

**进入条件**: ✅ 已满足

**预启动任务**:
{self._render_next_stage_tasks()}

---

## 六、附录

- 配置: `backend/docs/stage_config.yaml`
- 任务标记: `backend/docs/.task_done/`
- 执行日志: `backend/docs/stage_logs/`
"""

    def _render_next_stage_tasks(self) -> str:
        """渲染下一阶段任务清单"""
        next_id = self._next_stage()
        if not next_id:
            return "- (无, 全部阶段完成)"
        next_stage = self.stages[next_id]
        return "\n".join([
            f"- [ ] {t['id']}: {t['title']} ({'🤖 AI 可协助' if t.get('ai_assisted') else '👤 人工'})"
            for t in next_stage["tasks"][:5]
        ])

    def _update_progress_board(self):
        """更新 PROGRESS.md 看板"""
        lines = [
            "# 项目迭代进度看板",
            "",
            f"> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (自动)",
            f"> 当前阶段: [{self.current_stage_id}] {self.stages[self.current_stage_id]['name']}",
            "",
            "## 总览",
            "",
            "| 阶段 | 名称 | 状态 | 完成度 |",
            "|---|---|---|---|",
        ]

        total_all = 0
        done_all = 0
        for stage_id, stage in self.stages.items():
            tasks = stage["tasks"]
            done = sum(1 for t in tasks if is_task_done(t["id"]))
            total = len(tasks)
            total_all += total
            done_all += done
            percent = done / total * 100 if total > 0 else 0

            if done == total:
                icon = "✅ 完成"
            elif done == 0:
                icon = "⚪ 待启动"
            else:
                icon = "🔄 进行中"
            if stage_id == self.current_stage_id:
                icon += " ⬅️"

            lines.append(f"| {stage_id} | {stage['name']} | {icon} | {done}/{total} ({percent:.0f}%) |")

        overall = done_all / total_all * 100 if total_all > 0 else 0
        lines.extend([
            "",
            f"**整体完成度**: {done_all}/{total_all} ({overall:.1f}%)",
            "",
            "## 当前阶段详情",
            "",
            f"### [{self.current_stage_id}] {self.stages[self.current_stage_id]['name']}",
            "",
            f"**目标**: {self.stages[self.current_stage_id]['goal']}",
            "",
            "| 任务 | 标题 | 状态 | 完成时间 |",
            "|---|---|---|---|",
        ])

        for t in self.stages[self.current_stage_id]["tasks"]:
            marker = TASK_DONE_DIR / f"{t['id']}.done"
            if marker.exists():
                content = marker.read_text(encoding="utf-8")
                completed_at = ""
                for line in content.splitlines():
                    if line.startswith("completed_at:"):
                        completed_at = line.split(":", 1)[1].strip()
                status = f"✅ {completed_at}"
            else:
                status = "⬜ 待完成"
            lines.append(f"| {t['id']} | {t['title']} | {status} | - |")

        lines.extend([
            "",
            "---",
            "",
            "💡 标记完成: `python backend/scripts/stage_runner.py --mark-complete <TASK_ID>`",
            "📊 执行当前阶段: `python backend/scripts/stage_runner.py`",
            "📈 查看状态: `python backend/scripts/stage_runner.py --status`",
        ])

        PROGRESS_FILE.write_text("\n".join(lines), encoding="utf-8")

    # ---------------------------------------------------------------------
    # 状态变更
    # ---------------------------------------------------------------------
    def jump_to(self, stage_id: str):
        """跳到指定阶段 (强制)"""
        if stage_id not in self.stages:
            print(f"❌ 阶段 {stage_id} 不存在, 可选: {', '.join(self.stage_order)}")
            return
        self.config["current_stage"] = stage_id
        self.config["current_stage_status"] = "IN_PROGRESS"
        self.current_stage_id = stage_id
        save_config(self.config)
        print(f"⏭️  跳转到 [{stage_id}] {self.stages[stage_id]['name']}")
        self.run_stage(stage_id, force=True)

    def rollback(self):
        """回滚到上一阶段 (git revert)"""
        prev = self._prev_stage()
        if not prev:
            print(f"❌ 已在第一阶段, 无法回滚")
            return
        print(f"⏪ 回滚到 [{prev}] {self.stages[prev]['name']}")
        # 取消当前阶段所有任务标记
        for t in self.stages[self.current_stage_id]["tasks"]:
            unmark_task(t["id"])
        # 撤销到上一阶段最后一个 commit
        result = run_command("git log --oneline -10")
        print("最近 10 个 commits:")
        print(result.get("stdout", ""))
        # 更新 config
        self.config["current_stage"] = prev
        save_config(self.config)
        print(f"\n✅ 已回滚配置到 {prev}, 任务标记已清空")
        print(f"   提示: 如需代码回滚, 手动执行: git revert HEAD~N")

    def mark_complete(self, task_id: str, note: str = ""):
        """标记任务完成 (主入口)"""
        # 校验 task_id 存在
        all_tasks = []
        for stage in self.stages.values():
            all_tasks.extend([t["id"] for t in stage["tasks"]])
        if task_id not in all_tasks:
            print(f"❌ 任务 {task_id} 不存在")
            print(f"   所有任务: {', '.join(all_tasks[:10])}...")
            return

        mark_task_done(task_id, note)
        # 找到任务所属阶段
        for stage_id, stage in self.stages.items():
            for t in stage["tasks"]:
                if t["id"] == task_id:
                    # 更新看板
                    self._update_progress_board()
                    print(f"📊 看板已更新")
                    # 检查阶段是否完成
                    completion = self._compute_completion(stage)
                    if completion["percent"] >= stage["exit_threshold"]:
                        print(f"\n🎯 阶段 {stage_id} 全部任务完成 (完成度 {completion['percent']:.0f}%)")
                        print(f"   提示: 执行 `python stage_runner.py` 自动验证 + 推进到下一阶段")
                    return


# =============================================================================
# CLI 入口
# =============================================================================
def main():
    ensure_dirs()
    config = load_config()
    runner = StageRunner(config)

    parser = argparse.ArgumentParser(
        description="Stage Runner: 阶段编排器 (单人 + AI 协作)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python stage_runner.py                       # 执行当前阶段
  python stage_runner.py --status              # 查看进度
  python stage_runner.py --stage S1            # 执行 S1
  python stage_runner.py --mark-complete S1-T1 # 标记 S1-T1 完成
  python stage_runner.py --jump S2             # 跳到 S2 (强制)
  python stage_runner.py --rollback            # 回滚到上一阶段
  python stage_runner.py --force-next          # 强制推进
        """,
    )
    parser.add_argument("--status", action="store_true", help="查看整体进度")
    parser.add_argument("--stage", type=str, help="指定执行阶段 (如 S1)")
    parser.add_argument("--mark-complete", type=str, metavar="TASK_ID", help="标记任务完成")
    parser.add_argument("--note", type=str, default="", help="完成任务备注")
    parser.add_argument("--jump", type=str, help="跳到指定阶段 (强制)")
    parser.add_argument("--rollback", action="store_true", help="回滚到上一阶段")
    parser.add_argument("--force-next", action="store_true", help="强制推进 (跳过阈值)")
    parser.add_argument("--update-board", action="store_true", help="仅更新进度看板")

    args = parser.parse_args()

    if args.status:
        runner.status()
    elif args.mark_complete:
        runner.mark_complete(args.mark_complete, args.note)
    elif args.jump:
        runner.jump_to(args.jump)
    elif args.rollback:
        runner.rollback()
    elif args.update_board:
        runner._update_progress_board()
        print(f"📊 看板已更新: {PROGRESS_FILE}")
    else:
        # 默认: 执行当前阶段
        force = args.force_next or args.stage is not None
        runner.run_stage(args.stage, force=force)


if __name__ == "__main__":
    main()
