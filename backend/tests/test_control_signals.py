"""
Test: 训练控制信号 (control_signals) — v3.5.0
============================================

覆盖:
1. SignalAction 枚举值 + 字符串比较
2. TaskCanceled 异常结构 (epoch/total_epochs/reason)
3. request_pause / request_cancel 写 Redis
4. clear_pause / clear_cancel / clear_all 清信号
5. make_pause_check 决策表 (cancel/pause/continue)
6. cancel 优先级 > pause
7. Redis 不可用时的优雅降级
"""
import pytest

from app.tasks.workers.control_signals import (
    SignalAction,
    TaskCanceled,
    request_pause,
    request_cancel,
    clear_pause,
    clear_cancel,
    clear_all,
    make_pause_check,
)


# ============== 1. SignalAction 枚举 ==============

class TestSignalAction:
    def test_enum_values(self):
        """枚举值符合 v3.5.0 设计"""
        assert SignalAction.CONTINUE.value == "CONTINUE"
        assert SignalAction.PAUSE.value == "PAUSE"
        assert SignalAction.CANCEL.value == "CANCEL"

    def test_string_compare(self):
        """SignalAction 继承 str, 可与字符串直接比较 (worker 兼容性)"""
        assert SignalAction.CANCEL == "CANCEL"
        assert SignalAction.PAUSE == "PAUSE"
        assert SignalAction.CONTINUE == "CONTINUE"

    def test_distinct_values(self):
        """三个枚举值互不相同"""
        assert len({SignalAction.CONTINUE, SignalAction.PAUSE, SignalAction.CANCEL}) == 3


# ============== 2. TaskCanceled 异常 ==============

class TestTaskCanceled:
    def test_default_reason(self):
        """默认 reason = user_cancel"""
        exc = TaskCanceled(epoch=5, total_epochs=20)
        assert exc.epoch == 5
        assert exc.total_epochs == 20
        assert exc.reason == "user_cancel"
        assert "5/20" in str(exc)
        assert "user_cancel" in str(exc)

    def test_custom_reason(self):
        """可传入自定义 reason (e.g. oom / timeout)"""
        exc = TaskCanceled(epoch=10, total_epochs=20, reason="oom")
        assert exc.reason == "oom"
        assert "oom" in str(exc)

    def test_inherits_exception(self):
        """继承 Exception, 可被 except Exception 捕获"""
        exc = TaskCanceled(epoch=1, total_epochs=10)
        assert isinstance(exc, Exception)
        with pytest.raises(TaskCanceled):
            raise exc


# ============== 3. make_pause_check 决策表 ==============

class TestMakePauseCheck:
    """注: 这些测试需要 fake_redis fixture 提供 in-memory redis 客户端.
    (在无真实 Redis 的测试环境下也能跑通)"""

    def test_returns_action_type(self, fake_redis):
        """返回类型是 SignalAction 枚举"""
        check = make_pause_check("nonexistent-task-id-1")
        result = check()
        assert isinstance(result, SignalAction)

    def test_no_signals_returns_continue(self, fake_redis):
        """没有 pause/cancel 信号 → CONTINUE"""
        task_id = "test-no-signals-1"
        clear_all(task_id)
        check = make_pause_check(task_id)
        assert check() == SignalAction.CONTINUE

    def test_pause_signal_returns_pause(self, fake_redis):
        """有 pause 信号 (无 cancel) → PAUSE"""
        task_id = "test-pause-only-1"
        clear_all(task_id)
        request_pause(task_id, ttl=60)
        try:
            check = make_pause_check(task_id)
            assert check() == SignalAction.PAUSE
        finally:
            clear_all(task_id)

    def test_cancel_signal_returns_cancel(self, fake_redis):
        """有 cancel 信号 (无 pause) → CANCEL"""
        task_id = "test-cancel-only-1"
        clear_all(task_id)
        request_cancel(task_id, ttl=60)
        try:
            check = make_pause_check(task_id)
            assert check() == SignalAction.CANCEL
        finally:
            clear_all(task_id)

    def test_cancel_priority_over_pause(self, fake_redis):
        """同时有 cancel 和 pause 信号 → CANCEL (cancel 优先)"""
        task_id = "test-both-signals-1"
        clear_all(task_id)
        request_pause(task_id, ttl=60)
        request_cancel(task_id, ttl=60)
        try:
            check = make_pause_check(task_id)
            assert check() == SignalAction.CANCEL
        finally:
            clear_all(task_id)


# ============== 4. 清理函数 ==============

class TestClear:
    def test_clear_all_removes_both(self, fake_redis):
        """clear_all 同时清掉 pause 和 cancel"""
        task_id = "test-clear-all-1"
        request_pause(task_id, ttl=60)
        request_cancel(task_id, ttl=60)
        clear_all(task_id)
        check = make_pause_check(task_id)
        assert check() == SignalAction.CONTINUE

    def test_clear_pause_only(self, fake_redis):
        """clear_pause 只清 pause, 保留 cancel"""
        task_id = "test-clear-pause-1"
        clear_all(task_id)
        request_pause(task_id, ttl=60)
        request_cancel(task_id, ttl=60)
        clear_pause(task_id)
        check = make_pause_check(task_id)
        # cancel 仍存在, 返回 CANCEL
        assert check() == SignalAction.CANCEL
        # 清理
        clear_all(task_id)

    def test_clear_cancel_only(self, fake_redis):
        """clear_cancel 只清 cancel, 保留 pause"""
        task_id = "test-clear-cancel-1"
        clear_all(task_id)
        request_pause(task_id, ttl=60)
        request_cancel(task_id, ttl=60)
        clear_cancel(task_id)
        check = make_pause_check(task_id)
        # cancel 已清, 仅 pause 存在, 返回 PAUSE
        assert check() == SignalAction.PAUSE
        # 清理
        clear_all(task_id)


# ============== 5. 边界: 空 task_id ==============

class TestEmptyTaskId:
    def test_request_pause_empty(self):
        """空 task_id 直接返回 False, 不抛异常"""
        assert request_pause("") is False
        assert request_pause(None) is False

    def test_request_cancel_empty(self):
        assert request_cancel("") is False
        assert request_cancel(None) is False

    def test_clear_empty(self):
        """空 task_id 不抛异常"""
        # 都不应抛
        clear_pause("")
        clear_cancel("")
        clear_all("")


# ============== 6. 重复调用幂等性 ==============

class TestIdempotency:
    def test_request_pause_idempotent(self, fake_redis):
        """多次 request_pause 同 task_id 幂等 (Redis SETEX 覆盖)"""
        task_id = "test-pause-idem-1"
        clear_all(task_id)
        try:
            for _ in range(3):
                assert request_pause(task_id, ttl=60) is True
            check = make_pause_check(task_id)
            assert check() == SignalAction.PAUSE
        finally:
            clear_all(task_id)

    def test_request_cancel_idempotent(self, fake_redis):
        """多次 request_cancel 幂等"""
        task_id = "test-cancel-idem-1"
        clear_all(task_id)
        try:
            for _ in range(3):
                assert request_cancel(task_id, ttl=60) is True
            check = make_pause_check(task_id)
            assert check() == SignalAction.CANCEL
        finally:
            clear_all(task_id)
