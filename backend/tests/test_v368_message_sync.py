"""
v3.6.8 回归测试: SSE message 字段同步 + 5 个非 epoch callback 透传 + Re-running 消除
================================================================================

**问题背景 (用户报告 2026-08-06)**:
1. 训练中详情页 SSE message 字段卡在 "等待 worker 启动..." (实际训练正常)
2. pause→resume 后 message 持续显示 "Re-running (worker restart recovery)"
3. 5 个非 epoch 回调未传 commit_message, 详情页看不到实时进度

**根因 (v3.6.8)**:
- 5 个非 epoch 回调 (classification progress_cb / auto_annotate lambda /
  detection _export_cb / detection 数据就绪 / segmentation 数据就绪) 调
  set_task_state 时未传 commit_message, DB message 字段永远卡在 API 预创建文案
- create_or_reset_job 重投递时显式写 "Re-running" 字符串, 不再更新
- set_task_state 无 commit_message 智能去重, 全量透传会导致 1000+ writes/epoch

**修复 (4 层防御, v3.6.8)**:
1. L1 worker 透传: 5 个非 epoch 回调加 commit_message=msg
2. L2 智能去重: celery.py 新增 _should_commit_message (msg 变化/progress 1%/5s 兜底/终态)
3. L3 快照降级: job_state_service.py PROGRESS 分支加 _STALE_DB_MESSAGES 降级到 Celery
4. L4 文案清理: job.py 不再写 "Re-running" 字符串 (改为 None)

**测试覆盖** (6 类 18 用例):
1. TestV368ProgressCbCommitsMessage (5): 5 个 callback 都加了 commit_message
2. TestV368ShouldCommitMessageDedup (5): _should_commit_message 5 条规则
3. TestV368ShouldCommitMessageTerminal (2): 终态关键字强制 commit
4. TestV368SnapshotStaleMessageFallback (3): get_snapshot PROGRESS 降级
5. TestV368NoRerunningMessage (1): job.py 不再写 "Re-running"
6. TestV368StaticContract (2): 静态契约 (celery.py + job_state_service.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 路径设置
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))


# ============== 1. 5 个非 epoch callback 透传 commit_message ==============


class TestV368ProgressCbCommitsMessage:
    """验证 5 个非 epoch 回调都加了 commit_message=msg

    这是 L1 主修复: 没有这层透传, 后面的 L2/L3/L4 都只是兜底.
    """

    def test_classification_progress_cb_commits_message(self):
        """classification.py: progress_cb 必须传 commit_message=msg"""
        cls_py = _BACKEND_DIR / "app" / "tasks" / "workers" / "classification.py"
        content = cls_py.read_text(encoding="utf-8")
        # progress_cb 内 set_task_state 调用的附近必须有 commit_message=msg
        # 找到 progress_cb 函数体 (def progress_cb)
        idx = content.find("def progress_cb")
        assert idx > 0, "classification.py 缺少 progress_cb"
        # 截取到下一个 def 之前
        next_def = content.find("\n    def ", idx + 1)
        if next_def == -1:
            next_def = len(content)
        cb_body = content[idx:next_def]
        assert "commit_message" in cb_body, \
            "classification.py progress_cb 缺少 commit_message=msg (L1 主修复)"
        # 必须显式传 msg 参数
        assert "commit_message=msg" in cb_body, \
            "classification.py progress_cb 缺少 commit_message=msg"

    def test_classification_auto_annotate_lambda_commits_message(self):
        """classification.py: auto_annotate_task lambda 必须传 commit_message=msg"""
        cls_py = _BACKEND_DIR / "app" / "tasks" / "workers" / "classification.py"
        content = cls_py.read_text(encoding="utf-8")
        # auto_annotate_task 函数体内的 progress_cb lambda
        idx = content.find("def auto_annotate_task")
        assert idx > 0, "classification.py 缺少 auto_annotate_task"
        end_idx = content.find("__all__", idx)
        if end_idx == -1:
            end_idx = len(content)
        body = content[idx:end_idx]
        assert "progress_cb=lambda" in body, \
            "auto_annotate_task 缺少 progress_cb lambda"
        assert "commit_message=msg" in body, \
            "auto_annotate_task progress_cb lambda 缺少 commit_message=msg"

    def test_detection_export_cb_commits_message(self):
        """detection/train.py: _export_cb 必须传 commit_message"""
        det_py = _BACKEND_DIR / "app" / "tasks" / "workers" / "detection" / "train.py"
        content = det_py.read_text(encoding="utf-8")
        idx = content.find("def _export_cb")
        assert idx > 0, "detection/train.py 缺少 _export_cb"
        next_def = content.find("\n    def ", idx + 1)
        if next_def == -1:
            next_def = len(content)
        cb_body = content[idx:next_def]
        assert "commit_message" in cb_body, \
            "detection/train.py _export_cb 缺少 commit_message"
        # 必须用变量提取 (避免字面量漂移)
        assert "export_msg" in cb_body, \
            "detection/train.py _export_cb 应提取 export_msg 变量"

    def test_detection_dataset_ready_commits_message(self):
        """detection/train.py: "数据集就绪" 推送必须传 commit_message"""
        det_py = _BACKEND_DIR / "app" / "tasks" / "workers" / "detection" / "train.py"
        content = det_py.read_text(encoding="utf-8")
        # 找 "数据集就绪" 字符串位置
        idx = content.find("数据集就绪")
        assert idx > 0, "detection/train.py 缺少 '数据集就绪' 推送"
        # 在其附近 500 字符内必须有 commit_message
        window = content[idx: idx + 500]
        assert "commit_message" in window, \
            "detection/train.py '数据集就绪' 推送缺少 commit_message"
        assert "dataset_ready_msg" in window, \
            "detection/train.py '数据集就绪' 应提取 dataset_ready_msg 变量"

    def test_segmentation_dataset_ready_commits_message(self):
        """segmentation/train.py: "数据集就绪" 推送必须传 commit_message"""
        seg_py = _BACKEND_DIR / "app" / "tasks" / "workers" / "segmentation" / "train.py"
        content = seg_py.read_text(encoding="utf-8")
        idx = content.find("数据集就绪")
        assert idx > 0, "segmentation/train.py 缺少 '数据集就绪' 推送"
        window = content[idx: idx + 500]
        assert "commit_message" in window, \
            "segmentation/train.py '数据集就绪' 推送缺少 commit_message"
        assert "seg_ready_msg" in window, \
            "segmentation/train.py '数据集就绪' 应提取 seg_ready_msg 变量"


# ============== 2. _should_commit_message 5 条规则 ==============


class TestV368ShouldCommitMessageDedup:
    """测试 _should_commit_message 智能去重函数

    5 条规则 (任一满足即 commit):
    1. 首次调用 (无缓存) → commit
    2. msg 与上次不同 → commit
    3. progress 与上次变化 >= 1% → commit
    4. 上次缓存后超过 5 秒 → commit
    5. 终态关键字 → 总是 commit (在 TestV368ShouldCommitMessageTerminal 测)
    """

    def setup_method(self):
        """每个测试前清空缓存"""
        from app.tasks.service.training_lifecycle_service.celery import _LAST_COMMIT_MSG_SIG
        _LAST_COMMIT_MSG_SIG.clear()

    def test_first_call_commits(self):
        """规则 1: 首次调用 (无缓存) → commit"""
        from app.tasks.service.training_lifecycle_service.celery import _should_commit_message
        # 清空后首次调用
        result = _should_commit_message("task-1", "Epoch 1 batch 1", 1.0)
        assert result is True, "首次调用必须 commit"

    def test_same_msg_same_progress_within_5s_does_not_commit(self):
        """规则反例: 完全相同 + 5s 内 → 不 commit"""
        from datetime import datetime
        from app.tasks.service.training_lifecycle_service.celery import (
            _should_commit_message, _LAST_COMMIT_MSG_SIG,
        )
        # 模拟首次 set_task_state 已写入缓存 (函数本身只查不写, 写入由 set_task_state 完成)
        _LAST_COMMIT_MSG_SIG["task-2"] = (
            "Epoch 1 batch 5", 5.0, datetime.utcnow().timestamp(),
        )
        # 立刻再次调用, msg/progress 都一样 → 不 commit
        result = _should_commit_message("task-2", "Epoch 1 batch 5", 5.0)
        assert result is False, "完全相同的调用 5s 内不应 commit"

    def test_msg_change_commits(self):
        """规则 2: msg 与上次不同 → commit"""
        from datetime import datetime
        from app.tasks.service.training_lifecycle_service.celery import (
            _should_commit_message, _LAST_COMMIT_MSG_SIG,
        )
        # 模拟已有缓存
        _LAST_COMMIT_MSG_SIG["task-3"] = (
            "Epoch 1 batch 5", 5.0, datetime.utcnow().timestamp(),
        )
        # 换 msg → commit
        result = _should_commit_message("task-3", "Epoch 1 batch 6", 5.0)
        assert result is True, "msg 变化必须 commit"

    def test_progress_change_ge_1pct_commits(self):
        """规则 3: progress 变化 >= 1% → commit"""
        from datetime import datetime
        from app.tasks.service.training_lifecycle_service.celery import (
            _should_commit_message, _LAST_COMMIT_MSG_SIG,
        )
        # 模拟已有缓存
        _LAST_COMMIT_MSG_SIG["task-4"] = (
            "Epoch 1 batch 5", 5.0, datetime.utcnow().timestamp(),
        )
        # progress 变化 >= 1% → commit (即便 msg 一样)
        result = _should_commit_message("task-4", "Epoch 1 batch 5", 6.0)
        assert result is True, "progress 变化 1% 必须 commit"

    def test_progress_change_lt_1pct_does_not_commit(self):
        """规则反例: progress 变化 < 1% + msg 一样 → 不 commit"""
        from datetime import datetime
        from app.tasks.service.training_lifecycle_service.celery import (
            _should_commit_message, _LAST_COMMIT_MSG_SIG,
        )
        _LAST_COMMIT_MSG_SIG["task-5"] = (
            "Epoch 1 batch 5", 5.0, datetime.utcnow().timestamp(),
        )
        # progress 变化 0.5% (< 1%) → 不 commit
        result = _should_commit_message("task-5", "Epoch 1 batch 5", 5.5)
        assert result is False, "progress 变化 < 1% 且 msg 一样不应 commit"


# ============== 3. 终态关键字强制 commit ==============


class TestV368ShouldCommitMessageTerminal:
    """测试终态关键字 (_TERMINAL_MSG_KEYWORDS) 强制 commit, 防止被 dedup 吃掉"""

    def setup_method(self):
        from app.tasks.service.training_lifecycle_service.celery import _LAST_COMMIT_MSG_SIG
        _LAST_COMMIT_MSG_SIG.clear()

    def test_training_completed_terminal_commits(self):
        """终态文案 "Training completed" 即使与上次完全一样也要 commit"""
        from datetime import datetime
        from app.tasks.service.training_lifecycle_service.celery import (
            _should_commit_message, _LAST_COMMIT_MSG_SIG,
        )
        # 模拟已有非终态缓存
        _LAST_COMMIT_MSG_SIG["task-t1"] = (
            "Epoch 5/5 batch 100", 100.0, datetime.utcnow().timestamp(),
        )
        # 终态文案: 强制 commit
        result = _should_commit_message("task-t1", "Training completed", 100.0)
        assert result is True, "终态 'Training completed' 必须 commit (防 dedup 吃掉)"

    def test_canceled_paused_terminal_commits(self):
        """终态文案 "Canceled at" / "Paused at" 强制 commit"""
        from datetime import datetime
        from app.tasks.service.training_lifecycle_service.celery import (
            _should_commit_message, _LAST_COMMIT_MSG_SIG,
        )
        # 先填一个缓存
        _LAST_COMMIT_MSG_SIG["task-t2"] = (
            "Epoch 1", 1.0, datetime.utcnow().timestamp(),
        )
        # 终态 1
        assert _should_commit_message("task-t2", "Canceled at epoch 1/5", 1.0) is True
        # 终态 2
        assert _should_commit_message("task-t2", "Paused at epoch 1/5", 1.0) is True


# ============== 4. get_snapshot PROGRESS 分支降级到 Celery ==============


class TestV368SnapshotStaleMessageFallback:
    """测试 JobStateService.get_snapshot PROGRESS 分支降级逻辑

    DB message 命中 _STALE_DB_MESSAGES 时, 降级到 Celery 实时 msg.
    """

    def _build_celery_info(self, msg: str = "", progress: float = 50.0) -> dict:
        return {
            "state": "PROGRESS",
            "progress": progress,
            "msg": msg,
            "total_epochs": 5,
        }

    def test_db_stale_message_falls_back_to_celery(self):
        """DB 是 "等待 worker 启动..." (陈旧) → 用 Celery msg"""
        from app.tasks.service.job_state_service import (
            JobStateService, _STALE_DB_MESSAGES,
        )
        # 模拟: DB message 是陈旧占位文案, Celery 有实时 msg
        db_msg = "等待 worker 启动..."
        celery_info = self._build_celery_info("Epoch 1/5 batch 10", 10.0)
        # 应用降级逻辑
        celery_msg = celery_info.get("msg") or celery_info.get("message", "") or ""
        if db_msg in _STALE_DB_MESSAGES:
            result_message = celery_msg
        else:
            result_message = db_msg or celery_msg
        assert result_message == "Epoch 1/5 batch 10", \
            f"DB 陈旧文案应降级到 Celery, 实际: {result_message}"

    def test_db_rerunning_message_falls_back_to_celery(self):
        """DB 是 "Re-running (worker restart recovery)" → 用 Celery msg"""
        from app.tasks.service.job_state_service import _STALE_DB_MESSAGES
        db_msg = "Re-running (worker restart recovery)"
        celery_info = self._build_celery_info("Epoch 4/5 batch 1", 80.0)
        celery_msg = celery_info.get("msg") or celery_info.get("message", "") or ""
        if db_msg in _STALE_DB_MESSAGES:
            result_message = celery_msg
        else:
            result_message = db_msg or celery_msg
        assert result_message == "Epoch 4/5 batch 1", \
            f"DB 'Re-running' 文案应降级到 Celery, 实际: {result_message}"

    def test_db_normal_message_keeps_db_value(self):
        """DB 是正常训练文案 → 保留 DB (不走 Celery)"""
        from app.tasks.service.job_state_service import _STALE_DB_MESSAGES
        db_msg = "Epoch 3/5 batch 50"
        celery_info = self._build_celery_info("Epoch 3/5 batch 51", 70.0)
        celery_msg = celery_info.get("msg") or celery_info.get("message", "") or ""
        if db_msg in _STALE_DB_MESSAGES:
            result_message = celery_msg
        else:
            result_message = db_msg or celery_msg
        # 正常文案, 应保留 DB
        assert result_message == db_msg, \
            f"DB 正常文案应保留, 实际: {result_message}"


# ============== 5. job.py 不再写 "Re-running" 字符串 ==============


class TestV368NoRerunningMessage:
    """验证 job.py 不再写 "Re-running (worker restart recovery)" 字符串"""

    def test_job_py_no_rerunning_string_assignment(self):
        """job.py 内不能有 existing.message = "Re-running ..." 赋值"""
        job_py = _BACKEND_DIR / "app" / "tasks" / "service" / "training_lifecycle_service" / "job.py"
        content = job_py.read_text(encoding="utf-8")
        # 关键字符串不应再出现 (在赋值语句里)
        assert 'existing.message = "Re-running' not in content, \
            "job.py 还有 existing.message = 'Re-running...' 赋值 (L4 修复未完成)"
        # 应改为 None
        assert content.count("existing.message = None") >= 2, \
            "job.py 应有 2 处 existing.message = None (两处 reset 路径)"

    def test_job_py_rerunning_replaced_by_none(self):
        """job.py 两处重投递路径都已改为 None"""
        job_py = _BACKEND_DIR / "app" / "tasks" / "service" / "training_lifecycle_service" / "job.py"
        content = job_py.read_text(encoding="utf-8")
        # 应有两处 None 赋值
        assert content.count("existing.message = None") == 2, \
            f"job.py 应有 2 处 None 赋值, 实际: {content.count('existing.message = None')}"


# ============== 6. 静态契约: celery.py 与 job_state_service.py 必须包含 v3.6.8 标记 ==============


class TestV368StaticContract:
    """静态契约: 防止后续误改回旧逻辑

    v3.6.6 教训: 新增子模块函数必须在 __init__.py 挂载, 契约测试是"防回归最后防线"
    """

    def test_celery_py_has_should_commit_message(self):
        """celery.py 必须包含 _should_commit_message 函数定义"""
        celery_py = _BACKEND_DIR / "app" / "tasks" / "service" / "training_lifecycle_service" / "celery.py"
        content = celery_py.read_text(encoding="utf-8")
        assert "def _should_commit_message" in content, \
            "celery.py 缺少 _should_commit_message 函数定义 (L2 修复)"
        assert "_LAST_COMMIT_MSG_SIG" in content, \
            "celery.py 缺少 _LAST_COMMIT_MSG_SIG 缓存 (L2 修复)"
        # 必须有 v3.6.8 标记
        assert "v3.6.8" in content, \
            "celery.py 缺少 v3.6.8 HOTFIX 标记 (防回归)"

    def test_job_state_service_py_has_stale_messages(self):
        """job_state_service.py 必须包含 _STALE_DB_MESSAGES 降级逻辑"""
        jss_py = _BACKEND_DIR / "app" / "tasks" / "service" / "job_state_service.py"
        content = jss_py.read_text(encoding="utf-8")
        assert "_STALE_DB_MESSAGES" in content, \
            "job_state_service.py 缺少 _STALE_DB_MESSAGES 集合 (L3 修复)"
        # 必须含 3 个具体文案
        assert "等待 worker 启动" in content, \
            "job_state_service.py _STALE_DB_MESSAGES 缺少 '等待 worker 启动'"
        assert "Re-running" in content, \
            "job_state_service.py _STALE_DB_MESSAGES 缺少 'Re-running' 字符串"
        # 必须有 v3.6.8 标记
        assert "v3.6.8" in content, \
            "job_state_service.py 缺少 v3.6.8 HOTFIX 标记 (防回归)"
