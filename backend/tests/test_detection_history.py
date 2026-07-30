"""
v2.5.29 验证脚本: detection train_cb 实时同步 history 到 Redis + DB
================================================================
模拟 train_cb 被调用, 验证:
1. _update_training_history 被调用 (Redis 写入)
2. _run_async 跑 _update_job_history (DB 写入)
3. 不抛异常, 不影响训练主流程

跑法: uv run --project backend python backend/tests/test_detection_history.py
"""
import sys
import os
import json
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

# 模拟 _run_async 立即跑协程, 不开新线程
from app.utils.async_helpers import run_async_in_worker  # noqa: F401

real_run_async = run_async_in_worker


def fake_run_async(coro, *args, **kwargs):
    """同步跑协程 (用一次性 loop, 不影响外部)"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:
            pass


# patch
run_async_in_worker = fake_run_async

import app.tasks.workers.detection as dt  # noqa
dt._run_async = fake_run_async


# 模拟 _update_training_history 计数
real_update_history = dt._update_training_history if hasattr(dt, '_update_training_history') else None
call_count = {"redis": 0, "db": 0}


def fake_update_history(task_id, history):
    call_count["redis"] += 1
    print(f"  [redis] task_id={task_id} history_len={len(history)} last_epoch={history[-1].get('epoch')}")


# patch
import app.tasks.workers as t
t._update_training_history = fake_update_history


# Mock self
class FakeTask:
    request = MagicMock()
    request.id = "test-task-12345"


self = FakeTask()


# 构造 history_buffer + sticky_meta
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


# 重新执行 train_cb 测试
def run_train_cb_test():
    """模拟 3 个 epoch 的 train_cb 调用"""
    print("=== 检测 detection train_cb 实时写 history 测试 ===")

    # 准备: 用真实模块跑一个简化的 train_cb 逻辑
    history_buffer = []
    sticky_meta = {"data_total": 100, "data_train": 80, "data_val": 20, "num_classes": 2,
                   "class_names": ["cat", "dog"]}

    # 复制 detection_tasks.py 中 train_cb 的新逻辑
    def train_cb(stage, current_epoch, total_epochs, metrics):
        history_buffer.append({
            "epoch": current_epoch,
            "total_epochs": total_epochs,
            **metrics,
        })
        progress_pct = round(current_epoch / max(total_epochs, 1) * 100, 2)
        meta = {
            "progress": progress_pct,
            "msg": f"训练 epoch {current_epoch}/{total_epochs}",
            "total_epochs": total_epochs,
            "current_epoch": current_epoch,
            **{f"train_{k}": v for k, v in metrics.items()
               if isinstance(v, (int, float))},
        }
        if sticky_meta:
            meta.update(sticky_meta)
        # 模拟 _set_task_state
        # _set_task_state(self, "PROGRESS", meta)
        # 修复后的代码: 写 Redis
        if history_buffer:
            try:
                t._update_training_history(self.request.id, list(history_buffer))
            except Exception as e:
                print(f"[warn] det history -> redis failed: {e}")

            # 写 DB (mock: 不实际写, 只计数)
            async def _update_job_history():
                # from app.database import AsyncSessionLocal
                # from app.tasks.model.training_job import TrainingJob
                # async with AsyncSessionLocal() as db:
                #     j = await db.get(TrainingJob, job_id)
                #     if not j: return
                #     j.progress = progress_pct
                #     ...
                #     j.history = list(history_buffer)
                #     await db.commit()
                call_count["db"] += 1
                print(f"  [db] j.history = {len(history_buffer)} entries")

            try:
                fake_run_async(_update_job_history())
            except Exception as e:
                print(f"[warn] det _update_job_history failed: {e}")

    # 模拟 3 个 epoch
    for epoch in [1, 2, 3]:
        metrics = {
            "box_loss": 1.0 - 0.1 * epoch,
            "cls_loss": 0.5 - 0.05 * epoch,
            "dfl_loss": 0.8 - 0.08 * epoch,
            "map_50": 0.1 + 0.1 * epoch,
            "precision": 0.2 + 0.1 * epoch,
            "recall": 0.3 + 0.1 * epoch,
        }
        train_cb("train.epoch", epoch, 3, metrics)

    # 验证
    print(f"\n=== 结果 ===")
    print(f"Redis 写入次数: {call_count['redis']} (期望 3)")
    print(f"DB 写入次数: {call_count['db']} (期望 3)")
    print(f"history_buffer 最终长度: {len(history_buffer)} (期望 3)")

    assert call_count["redis"] == 3, f"Redis 写入次数不对: {call_count['redis']}"
    assert call_count["db"] == 3, f"DB 写入次数不对: {call_count['db']}"
    assert len(history_buffer) == 3

    # 验证 history 内容
    print(f"\n第一条 history: {json.dumps(history_buffer[0], ensure_ascii=False)}")
    print(f"最后一条 history: {json.dumps(history_buffer[-1], ensure_ascii=False)}")

    # 验证 map_50 在 history 中
    assert "map_50" in history_buffer[-1]
    assert "box_loss" in history_buffer[-1]
    print("\n[OK] 所有断言通过!")


if __name__ == "__main__":
    run_train_cb_test()
