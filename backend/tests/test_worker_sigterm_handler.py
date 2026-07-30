"""
Worker SIGTERM handler tests (P0-1 from docs/38-后端架构现状评估与拆分部署方案.md)
==================================================================================

落档 P0-1 提出的问题: worker 进程收到 SIGTERM (来自
``celery_app.control.revoke(job_id, terminate=True, signal="SIGTERM")``)
走 Python 默认处理, 不写 emergency checkpoint, 不收尾.

修复方向:
- 在 worker 启动前注册 SIGTERM handler
- handler 写 ``train_emergency:{task_id}`` 到 Redis (含 reason + timestamp)
- 不破坏正常 Ctrl+C (SIGINT) 退出

测试策略:
- **不真的 fork worker 子进程** (太重, 且依赖真 Redis)
- 用 mock 覆盖 ``signal.signal`` + Python signal 触发, 验证 handler 被装入 + 触发时副作用
"""
from __future__ import annotations

import signal
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


class TestSignalHandlerInstallation:
    """测试 helper 装入 SIGTERM handler 的行为."""

    def test_helper_imports_clean(self):
        """smoke: 模块可以 import, 函数存在."""
        from app.tasks.workers.signal_handlers import install_sigterm_handler
        assert callable(install_sigterm_handler)

    def test_install_sigterm_handler_calls_signal_signal(self):
        """必须调用 signal.signal(SIGTERM, ...) 把 handler 装进进程."""
        from app.tasks.workers.signal_handlers import install_sigterm_handler

        celery_app = MagicMock()
        installed = {}
        real_signal = signal.signal

        def fake_signal(signum, handler):
            installed[signum] = handler
            return real_signal(signum, signal.SIG_DFL)  # 真正注册, 不破坏其它测试

        with patch("app.tasks.workers.signal_handlers.signal.signal", side_effect=fake_signal):
            install_sigterm_handler(celery_app)

        assert signal.SIGTERM in installed, (
            f"SIGTERM handler 没注册, 实际注册了 {list(installed.keys())}"
        )
        assert callable(installed[signal.SIGTERM]), "SIGTERM handler 必须是 callable"

    def test_install_sigterm_handler_does_not_overwrite_sigint(self):
        """不能覆盖 SIGINT (Ctrl+C), 否则 start_workers.py 的 Ctrl+C 优雅退出失效."""
        from app.tasks.workers.signal_handlers import install_sigterm_handler

        installed = {}
        original_handlers = {}

        def fake_signal(signum, handler):
            original_handlers[signum] = signal.getsignal(signum)
            installed[signum] = handler
            return signal.SIG_DFL

        with patch("app.tasks.workers.signal_handlers.signal.signal", side_effect=fake_signal):
            install_sigterm_handler(MagicMock())

        assert signal.SIGTERM in installed
        # SIGINT 不应被安装 (但 fake_signal 不返回它的旧 handler, 因为我们没 patch signal.getsignal)
        # 直接断言 "不在 installed" 即可
        assert signal.SIGINT not in installed, (
            f"SIGINT 重复装了, 实际 {signal.SIGINT in installed}"
        )


class TestSigtermHandlerTriggersEmergencyWrite:
    """测试 SIGTERM handler 真正触发时的副作用."""

    def _get_installed_handler(self):
        """装入并返回安装的 SIGTERM handler (用于直接调用模拟)."""
        from app.tasks.workers.signal_handlers import install_sigterm_handler

        captured = {}
        def fake_signal(signum, handler):
            captured[signum] = handler
            return signal.SIG_DFL

        celery_app = MagicMock()
        with patch("app.tasks.workers.signal_handlers.signal.signal", side_effect=fake_signal):
            install_sigterm_handler(celery_app)

        return captured[signal.SIGTERM]

    def test_handler_does_not_raise(self):
        """handler 在收到 SIGTERM 时不能抛异常, 否则 worker 进程立刻退出."""
        handler = self._get_installed_handler()
        # 不带任何副作用调用: 必须返回 None/handler 自定义值, 不能抛
        try:
            handler(signal.SIGTERM, None)
        except Exception as e:
            pytest.fail(f"SIGTERM handler raised: {e!r}")

    def test_handler_writes_train_emergency_marker_when_task_active(self):
        """若有 active task, handler 必须写 ``train_emergency:{task_id}`` 到 Redis.

        这里 mock celery_app + 一个 redis-like 对象. 因为我们的设计是 "sigterm_handler
        从 celery_app 查 active_task, 然后通过 redis client 写", 这个 mock 测的是设计契约.
        """
        handler = self._get_installed_handler()

        active_task_id = "test-task-sig-001"
        mock_celery_app = MagicMock()
        # 模拟 celery internals: celery_app 如何暴露 "当前 active task"
        # 我们的设计选择 worker.active_tasks (mock), handler 会读它然后写 redis.

        mock_redis = MagicMock()
        # 设一个 contract: handler 调用 redis.set("train_emergency:<task_id>", ...)
        # 真实实现里, 我们需要一个 "获取 active task" 的抽象:
        # 这里 mock 一个简单接口

        with patch("app.tasks.workers.signal_handlers._get_active_task_id",
                   return_value=active_task_id), \
             patch("app.tasks.workers.signal_handlers._get_redis",
                   return_value=mock_redis):
            handler(signal.SIGTERM, mock_celery_app)

        # 应当至少调用一次 redis.set, key 含 train_emergency
        set_calls = mock_redis.set.call_args_list
        assert set_calls, f"handler 没调 redis.set, calls={mock_redis.method_calls}"
        keys_written = [c.args[0] for c in set_calls if c.args]
        assert any(k.startswith("train_emergency:") for k in keys_written), (
            f"handler 没写 train_emergency:*, 实际写入 keys={keys_written}"
        )

    def test_handler_skips_redis_write_when_no_active_task(self):
        """没有 active task 时, handler 不应抛异常, 也不应写 redis."""
        handler = self._get_installed_handler()
        mock_celery_app = MagicMock()
        mock_redis = MagicMock()

        with patch("app.tasks.workers.signal_handlers._get_active_task_id",
                   return_value=None), \
             patch("app.tasks.workers.signal_handlers._get_redis",
                   return_value=mock_redis):
            handler(signal.SIGTERM, mock_celery_app)

        # 没 active task: 不应写 redis
        assert mock_redis.set.call_count == 0, (
            f"没 active task 时不该写 redis, 实际写入 {mock_redis.set.call_args_list}"
        )


class TestCeleryWorkerStartupHooksHandler:
    """worker_main 启动时 (即 _worker_main() 被调) 必须装入 SIGTERM handler."""

    def test_worker_main_installs_sigterm_handler(self):
        from app.tasks.workers.celery_app import _worker_main

        with patch("app.tasks.workers.signal_handlers.install_sigterm_handler") as m_install, \
             patch("app.tasks.workers.celery_app.celery_app.worker_main") as m_wm:
            _worker_main(queues="train", concurrency=1)

        # 只要 _worker_main 走到 worker_main 之前装了 handler 即可
        assert m_install.called, (
            f"_worker_main 没调用 install_sigterm_handler. "
            f"实际 call stack: worker_main called={m_wm.called}, install called={m_install.called}"
        )
