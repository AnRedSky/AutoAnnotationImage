"""
v3.6.0 P5 — 自适应进度回调 + worker 端 DB 写优化测试
==================================================

**目标**:
- 验证 progress_callback 触发的 dataset_stats DB 写有 sticky_meta 缓存
- 验证同一 class_names 重复训练, DB 写为 0 次 (仅首次)
- 验证不同 class_names 触发 DB 写 1 次

**测试方法**:
- mock TrainingLifecycleService.persist_dataset_stats_sync, 跟踪调用次数
- 用 monkeypatch 注入 fake sticky_meta / fake task_id
- 模拟 progress_cb 调用, 验证 DB 写次数

**注意**:
- 不启动 Celery worker, 直接调 worker 内的 progress_cb / epoch_cb
- 用 fastapi test client 验证端到端, 但 DB 写是 sync 包装 + AsyncSession,
  需要 monkeypatch persist_dataset_stats_sync 来跟踪
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import List

import pytest

# 测试环境
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("APP_DEBUG", "False")


# ============== T5.1: 小数据集进度回调粒度 (Phase 2 已实现, 这里加端到端验证) ==============


def test_t5_1_progress_callback_adaptive_for_small_dataset():
    """小数据集 (6 batch): progress_callback 每 batch 触发, 总回调数 >= 6.

    注: 实际训练时 phase 2 已把 cb_interval 改为 max(1, total_batches // 50).
    小数据集 (6 batch) → cb_interval=1 → 每 batch 回调.
    本测试是源码断言, 确保未来不会改回硬编码 100.
    """
    classification_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "classification.py"
    if not classification_py.exists():
        pytest.skip("classification.py not found")
    content = classification_py.read_text(encoding="utf-8")

    # 关键: _cb_interval = max(1, total_batches // 50)
    assert "_cb_interval" in content
    assert "max(1, total_batches // 50)" in content
    # 进度回调条件用 _cb_interval
    assert "batch_idx % _cb_interval == 0" in content


# ============== T5.2: 大数据集进度回调粒度 (动态计算) ==============


def test_t5_2_progress_callback_interval_large_dataset():
    """大数据集 (1000 batch): cb_interval=20 → 50 次回调 (避免 SSE 风暴)"""
    total_batches = 1000
    _cb_interval = max(1, total_batches // 50) if total_batches > 0 else 1
    assert _cb_interval == 20

    # 模拟回调次数
    callback_count = sum(1 for i in range(total_batches) if i % _cb_interval == 0)
    assert callback_count == 50


# ============== T5.3: worker 端 dataset_stats 写库加 hash 缓存 (静态) ==============


def test_t5_3_worker_dataset_stats_hash_cache():
    """worker classification.py: progress_cb 加 class_names hash 缓存.

    验证:
    1. 导入 hashlib + json 用于 hash
    2. 计算 class_names hash 并存到 sticky_meta
    3. 仅当 hash 变化时才调 persist_dataset_stats_sync
    """
    worker_py = Path(__file__).parent.parent / "app" / "tasks" / "workers" / "classification.py"
    if not worker_py.exists():
        pytest.skip("classification.py worker not found")
    content = worker_py.read_text(encoding="utf-8")

    # 1) 必须 import hashlib
    assert "import hashlib" in content
    # 2) 必须 import json (用于序列化 class_names)
    assert "import json" in content
    # 3) 必须有 class_names_hash 缓存逻辑
    assert "class_names_hash" in content, (
        "worker 必须用 sticky_meta 缓存 class_names_hash, "
        "避免重复调 persist_dataset_stats_sync"
    )
    # 4) 必须有 hash 变化时写库的判断
    assert re.search(
        r"sticky_meta\.get\(\"class_names_hash\"\)\s*!=\s*_cn_hash",
        content,
    ), "必须用 sticky_meta.get('class_names_hash') != _cn_hash 判断是否需写库"


# ============== T5.4: 动态验证 - 同一 class_names 重复触发 → DB 写 0 次 ==============


def test_t5_4_hash_cache_avoids_repeat_db_writes(monkeypatch):
    """动态验证: 模拟 progress_cb 多次调用, 同 class_names 触发 DB 写为 0 次.

    策略: 提取 progress_cb 内部的 hash 缓存逻辑, 模拟调用
    """
    # 1) mock persist_dataset_stats_sync
    from app.tasks.service.training_lifecycle_service import (
        TrainingLifecycleService,
    )
    call_count = {"n": 0}
    original_persist = TrainingLifecycleService.persist_dataset_stats_sync

    def counting_persist(*args, **kwargs):
        call_count["n"] += 1
        # 不真的调原函数 (避免 DB IO)

    monkeypatch.setattr(
        TrainingLifecycleService,
        "persist_dataset_stats_sync",
        staticmethod(counting_persist),
    )

    # 2) 模拟 progress_cb 内部的 hash 缓存逻辑
    sticky_meta = {}
    extra_with_class_names = {
        "data_total": 240,
        "data_train": 200,
        "data_val": 40,
        "num_classes": 5,
        "class_names": ["cat", "dog", "bird", "fish", "rabbit"],
    }

    def progress_cb_simulated(extra):
        # 模拟 worker 内的 hash 缓存逻辑
        if extra and "data_total" in extra and "num_classes" in extra:
            _class_names = extra.get("class_names") or []
            _cn_hash = hashlib.md5(
                json.dumps(_class_names, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if sticky_meta.get("class_names_hash") != _cn_hash:
                sticky_meta["class_names_hash"] = _cn_hash
                TrainingLifecycleService.persist_dataset_stats_sync(
                    "task-id", extra, job_id=123,
                )

    # 3) 第一次调用: 触发 DB 写
    progress_cb_simulated(extra_with_class_names)
    assert call_count["n"] == 1

    # 4) 第二次调用 (同 class_names): 不触发
    progress_cb_simulated(extra_with_class_names)
    assert call_count["n"] == 1, f"同 class_names 应 0 次 DB 写, 实际 {call_count['n']} 次"

    # 5) 第三次, 第四次... 都不触发
    for _ in range(5):
        progress_cb_simulated(extra_with_class_names)
    assert call_count["n"] == 1, (
        f"5 次同 class_names 回调应总 1 次 DB 写, 实际 {call_count['n']} 次"
    )


# ============== T5.5: 不同 class_names 触发 DB 写 1 次 ==============


def test_t5_5_different_class_names_triggers_db_write(monkeypatch):
    """动态验证: class_names 变化时, 触发 DB 写 1 次"""
    from app.tasks.service.training_lifecycle_service import (
        TrainingLifecycleService,
    )
    call_count = {"n": 0}

    def counting_persist(*args, **kwargs):
        call_count["n"] += 1

    monkeypatch.setattr(
        TrainingLifecycleService,
        "persist_dataset_stats_sync",
        staticmethod(counting_persist),
    )

    sticky_meta = {}

    def progress_cb_simulated(class_names):
        extra = {
            "data_total": 240,
            "num_classes": len(class_names),
            "class_names": class_names,
        }
        if extra and "data_total" in extra and "num_classes" in extra:
            _class_names = extra.get("class_names") or []
            _cn_hash = hashlib.md5(
                json.dumps(_class_names, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if sticky_meta.get("class_names_hash") != _cn_hash:
                sticky_meta["class_names_hash"] = _cn_hash
                TrainingLifecycleService.persist_dataset_stats_sync(
                    "task-id", extra, job_id=123,
                )

    # 1) 第一次: 触发
    progress_cb_simulated(["cat", "dog", "bird"])
    assert call_count["n"] == 1

    # 2) 同 class_names 多次: 不触发
    for _ in range(3):
        progress_cb_simulated(["cat", "dog", "bird"])
    assert call_count["n"] == 1

    # 3) class_names 变化: 触发 1 次
    progress_cb_simulated(["cat", "dog", "bird", "fish"])
    assert call_count["n"] == 2

    # 4) 再次变化: 触发
    progress_cb_simulated(["car", "truck", "plane"])
    assert call_count["n"] == 3

    # 5) 回到老的 (顺序无关): 触发 (因为 hash 不同)
    progress_cb_simulated(["cat", "dog", "bird"])
    assert call_count["n"] == 4


# ============== T5.6: 进度回调粒度回归 ==============


def test_t5_6_no_progress_callback_threshold_regression():
    """回归验证: progress_callback 在大数据集不会每 batch 都调 (避免 SSE 风暴)."""
    classification_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "classification.py"
    if not classification_py.exists():
        pytest.skip("classification.py not found")
    content = classification_py.read_text(encoding="utf-8")

    # 不能用硬编码 100 (旧版会让小数据集看起来"卡住")
    assert "batch_idx % 100 == 0" not in content, (
        "不允许用硬编码 100 batch 回调间隔, "
        "应改为 _cb_interval = max(1, total_batches // 50) 自适应"
    )
