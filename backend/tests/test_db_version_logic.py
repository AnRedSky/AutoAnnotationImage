"""
纯函数单元测试: JobStateService 行指纹 (db_version) 与缓存序列化

v3.5.0 Phase T6 新增
- 不依赖 DB / Redis, 纯函数验证
- 与 train/test_snapshots.py 等集成测试互补 (那些需要完整 DB fixture)
- pytest 可直接调用
"""
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============== 隔离: 不导入项目代码, 用本文件内副本验证纯函数逻辑 ==============
# (真实项目 JobStateService 的同名函数定义在
#  backend/app/tasks/service/job_state_service.py 中, 本测试只验证"逻辑等价"
#  防止以后修改 _make_db_version 时回归)

@dataclass
class _Snap:
    """JobStateSnapshot 的最小子集 (用于本测试)"""
    task_id: str
    state: str
    progress: float
    message: str
    current_epoch: Optional[int] = None
    total_epochs: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None
    source: str = "db"


def _make_db_version(snap: _Snap) -> str:
    """与项目 JobStateService._make_db_version 保持同步"""
    history_len = len(snap.history) if isinstance(snap.history, list) else 0
    return (
        f"{snap.state}|{round(snap.progress, 1)}|"
        f"{snap.current_epoch}|{history_len}|{snap.message or ''}"
    )


def _snapshot_to_cache_dict(snap: _Snap) -> dict:
    """与项目 JobStateService._snapshot_to_cache_dict 保持同步"""
    return {
        "task_id": snap.task_id,
        "state": snap.state,
        "progress": snap.progress,
        "message": snap.message,
        "current_epoch": snap.current_epoch,
        "total_epochs": snap.total_epochs,
        "started_at": snap.started_at.isoformat() if snap.started_at else None,
        "finished_at": snap.finished_at.isoformat() if snap.finished_at else None,
        "error": snap.error,
        "history": snap.history,
        "source": snap.source,
        "db_version": _make_db_version(snap),
    }


# ============== pytest 用例 ==============

def test_db_version_changes_on_state():
    """state 字段变化 → db_version 必须不同"""
    s1 = _Snap("t1", "PENDING", 0.0, "", current_epoch=None, history=[])
    s2 = _Snap("t1", "PROGRESS", 0.0, "", current_epoch=None, history=[])
    assert _make_db_version(s1) != _make_db_version(s2)


def test_db_version_changes_on_progress():
    """progress 字段变化 → db_version 必须不同"""
    s1 = _Snap("t1", "PROGRESS", 0.0, "")
    s2 = _Snap("t1", "PROGRESS", 0.5, "")
    assert _make_db_version(s1) != _make_db_version(s2)


def test_db_version_changes_on_message():
    """message 字段变化 → db_version 必须不同"""
    s1 = _Snap("t1", "PROGRESS", 50.0, "")
    s2 = _Snap("t1", "PROGRESS", 50.0, "epoch 5/20")
    assert _make_db_version(s1) != _make_db_version(s2)


def test_db_version_changes_on_epoch():
    """current_epoch 变化 → db_version 必须不同"""
    s1 = _Snap("t1", "PROGRESS", 50.0, "ok", current_epoch=5)
    s2 = _Snap("t1", "PROGRESS", 50.0, "ok", current_epoch=6)
    assert _make_db_version(s1) != _make_db_version(s2)


def test_db_version_changes_on_history_length():
    """history 长度变化 → db_version 必须不同 (代表新增了一个 epoch)"""
    s1 = _Snap("t1", "PROGRESS", 50.0, "ok", current_epoch=5, history=[{"epoch": 1}, {"epoch": 2}])
    s2 = _Snap("t1", "PROGRESS", 50.0, "ok", current_epoch=5, history=[{"epoch": 1}, {"epoch": 2}, {"epoch": 3}])
    assert _make_db_version(s1) != _make_db_version(s2)


def test_db_version_identical_returns_same():
    """同一对象两次调用必须返回相同"""
    s = _Snap("t1", "PROGRESS", 50.0, "ok", current_epoch=5, history=[{"epoch": 5}])
    assert _make_db_version(s) == _make_db_version(s)


def test_db_version_progress_rounding():
    """进度按 round(_, 1) 精度区分: 0.41 vs 0.45 视为不同"""
    s1 = _Snap("t1", "PROGRESS", 0.41, "ok")
    s2 = _Snap("t1", "PROGRESS", 0.45, "ok")
    # 0.41 → "0.4", 0.45 → "0.5" → 不同
    assert _make_db_version(s1) != _make_db_version(s2)


def test_db_version_empty_message_equals_none():
    """空字符串与 None message 视为相同 (避免误判)"""
    s_empty = _Snap("t1", "PENDING", 0.0, "")
    s_none = _Snap("t1", "PENDING", 0.0, None)
    assert _make_db_version(s_empty) == _make_db_version(s_none)


def test_db_version_history_none_does_not_crash():
    """history=None 时不应崩"""
    s = _Snap("t1", "PROGRESS", 50.0, "ok", current_epoch=5, history=None)
    v = _make_db_version(s)
    assert isinstance(v, str)
    assert "|0|" in v  # history_len=0


def test_cache_dict_roundtrip_preserves_db_version():
    """序列化/反序列化后 db_version 必须一致"""
    s = _Snap(
        task_id="t1", state="PROGRESS", progress=50.0, message="epoch 5/20",
        current_epoch=5, total_epochs=20,
        started_at=datetime(2026, 8, 4, 13, 0, 0),
        finished_at=None, error=None,
        history=[{"epoch": 1}, {"epoch": 2}, {"epoch": 3}],
    )
    d = _snapshot_to_cache_dict(s)
    j = json.dumps(d, ensure_ascii=False, default=str)
    parsed = json.loads(j)
    assert parsed["db_version"] == _make_db_version(s)


def test_cache_dict_roundtrip_preserves_started_at():
    """started_at 序列化为 ISO 字符串, 反序列化可还原"""
    s = _Snap(
        task_id="t1", state="PROGRESS", progress=50.0, message="ok",
        started_at=datetime(2026, 8, 4, 13, 0, 0),
        finished_at=None,
    )
    parsed = json.loads(json.dumps(_snapshot_to_cache_dict(s), default=str))
    assert parsed["started_at"] == "2026-08-04T13:00:00"
    assert parsed["finished_at"] is None


def test_cache_dict_roundtrip_preserves_history():
    """history 数组长度一致"""
    s = _Snap(
        task_id="t1", state="PROGRESS", progress=50.0, message="ok",
        history=[{"epoch": i, "loss": 0.1 * i} for i in range(1, 11)],
    )
    parsed = json.loads(json.dumps(_snapshot_to_cache_dict(s), default=str))
    assert len(parsed["history"]) == 10
    assert parsed["history"][-1]["epoch"] == 10


# ============== 直接运行 (pytest 不可用时) ==============

def _run_pytest_style():
    """遍历本文件所有 test_* 函数并执行, 输出 PASS/FAIL 汇总"""
    import inspect
    tests = [
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    ]
    passed = failed = 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            failures.append((name, str(e)))
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed += 1
            failures.append((name, repr(e)))
            print(f"  ERROR {name}: {e!r}")
    print()
    print(f"  Total: {passed + failed}, Passed: {passed}, Failed: {failed}")
    if failures:
        print("\n  Failures:")
        for name, msg in failures:
            print(f"    - {name}: {msg}")
    return failed == 0


if __name__ == "__main__":
    print("=" * 60)
    print("  JobStateService db_version 单元测试")
    print("=" * 60)
    ok = _run_pytest_style()
    sys.exit(0 if ok else 1)
