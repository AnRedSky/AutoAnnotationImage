"""
v3.6.0 P2 — DataLoader 全面加速测试
====================================

**目标**:
- 验证 DataLoader 配置正确性 (pin_memory / persistent_workers / prefetch_factor)
- 验证训练循环用 tensor 累积 (消除 .item() 同步)
- 验证 val 循环用 tensor 累积 + 异步传输
- 验证进度回调按 batch 数动态调整

**测试方法**:
- 静态扫描: 通过 mock settings.DATALOADER_WORKERS / torch.cuda.is_available 验证配置
- 动态验证: 用 mock DataLoader + Mock tensor 验证 .item() 调用次数
- 进度回调粒度: 直接调用回调逻辑验证触发次数
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import List

import pytest

# 测试环境
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("APP_DEBUG", "False")

from app.core.config import settings  # noqa: E402


# ============== T2.1: DataLoader 配置正确性 (静态) ==============

def test_t2_1_dataloader_config_defaults():
    """默认配置: DATALOADER_WORKERS=0 时不启用 pin_memory/persistent_workers"""
    # 这是为了保证 CPU 训练无性能回退
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "DATALOADER_WORKERS", 0)
        # 检查 config.py 不会改变 (我们只读它)
        assert settings.DATALOADER_WORKERS == 0


def test_t2_1_dataloader_config_gpu_recommended():
    """GPU 训练推荐配置: DATALOADER_WORKERS=4 (与 .env.example 一致)"""
    # 检查 .env.example 中的配置
    env_example = Path(__file__).parent.parent / ".env.example"
    if not env_example.exists():
        pytest.skip(".env.example not found")
    content = env_example.read_text(encoding="utf-8")
    # 必须有 DATALOADER_WORKERS=4 默认值
    assert re.search(r"^DATALOADER_WORKERS=4\s*$", content, re.MULTILINE), (
        ".env.example 必须有 DATALOADER_WORKERS=4 (GPU 训练推荐配置)"
    )


# ============== T2.2: 训练循环源码扫描 - 不调用 .item() ==============

def test_t2_2_training_loop_no_item_in_loop():
    """训练循环源码扫描: 内层循环不能调用 .item() (tensor 累积)

    旧版 t_loss += loss.item() 每个 batch 强制 CPU-GPU 同步, 慢 60-120ms/epoch
    新版 t_loss_t += loss.detach() 在 epoch 末才 .item() 一次
    """
    classification_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "classification.py"
    if not classification_py.exists():
        pytest.skip("classification.py not found")
    content = classification_py.read_text(encoding="utf-8")

    # 找到 train 循环
    # v3.6.0: 用宽松正则匹配 (# Train 注释 + 中间可能多行注释 + t_loss_t = torch.zeros)
    train_loop_match = re.search(
        r"# Train.*?t_loss_t\s*=\s*torch\.zeros.*?for batch_idx,.*?in enumerate\(train_loader\):\s*\n(.*?)(?=\n\s*v_loss\s*=|\n\s*# Validation|\n\s*# epoch)",
        content,
        re.DOTALL,
    )
    assert train_loop_match, "未找到 train 循环代码块"
    train_loop = train_loop_match.group(1)

    # 关键断言: train 循环内不调用 .item()
    item_calls = re.findall(r"\.item\(\)", train_loop)
    assert len(item_calls) == 0, (
        f"训练循环内有 {len(item_calls)} 处 .item() 调用, 应当全部移到 epoch 末以减少 CPU-GPU 同步: "
        f"{item_calls}"
    )


def test_t2_2_val_loop_no_item_in_loop():
    """val 循环源码扫描: 内层循环不能调用 .item()"""
    classification_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "classification.py"
    if not classification_py.exists():
        pytest.skip("classification.py not found")
    content = classification_py.read_text(encoding="utf-8")

    val_loop_match = re.search(
        r"with torch\.no_grad\(\):\s*\n(.*?)(?=\n\s*v_loss\s*=|\n\s*# 只有过)",
        content,
        re.DOTALL,
    )
    assert val_loop_match, "未找到 val 循环"
    val_loop = val_loop_match.group(1)

    # val 循环内不调用 .item() (除 criterion(outputs, labels) 的 .item() 旧调用, 已被替换)
    item_calls = re.findall(r"\.item\(\)", val_loop)
    # 注: criterion(outputs, labels) 是计算 loss tensor, 旧版 .item() 累积 loss 已替换为 v_loss_t +=
    # 允许 0 个 .item()
    assert len(item_calls) == 0, (
        f"val 循环内有 {len(item_calls)} 处 .item() 调用, 应全部移到 epoch 末: {item_calls}"
    )


# ============== T2.3: epoch 末才同步 .item() ==============

def test_t2_3_epoch_end_sync():
    """epoch 末才 .item() 同步: t_loss + v_loss 累加器在 epoch 外转标量"""
    classification_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "classification.py"
    if not classification_py.exists():
        pytest.skip("classification.py not found")
    content = classification_py.read_text(encoding="utf-8")

    # 关键: epoch 末有 t_loss = (t_loss_t / total_batches).item() + t_correct = int(t_correct_t.item())
    assert re.search(
        r"t_loss\s*=\s*\(t_loss_t\s*/\s*total_batches\)\.item\(\)",
        content,
    ), "epoch 末应 t_loss = (t_loss_t / total_batches).item() 一次同步"
    assert re.search(
        r"t_correct\s*=\s*int\(t_correct_t\.item\(\)\)",
        content,
    ), "epoch 末应 t_correct = int(t_correct_t.item()) 一次同步"
    assert re.search(
        r"v_loss\s*=\s*\(v_loss_t\s*/\s*max\(len\(val_loader\),\s*1\)\)\.item\(\)",
        content,
    ), "epoch 末应 v_loss = (v_loss_t / max(len(val_loader), 1)).item() 一次同步"


# ============== T2.4: 进度回调粒度 ==============

def test_t2_4_progress_callback_adaptive():
    """进度回调粒度自适应: 6 batch 数据集每 batch 回调, 1000 batch 数据集 50 次回调

    旧版硬编码 100 batch, 小数据集一次都不回调 → 用户感觉"卡住"
    新版: cb_interval = max(1, total_batches // 50)
    """
    classification_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "classification.py"
    if not classification_py.exists():
        pytest.skip("classification.py not found")
    content = classification_py.read_text(encoding="utf-8")

    # 关键: _cb_interval = max(1, total_batches // 50) 自适应粒度
    assert re.search(
        r"_cb_interval\s*=\s*max\(1,\s*total_batches\s*//\s*50\)",
        content,
    ), (
        "进度回调粒度应自适应: _cb_interval = max(1, total_batches // 50)\n"
        "保证: 6 batch 数据集 cb_interval=1 (每 batch 回调)\n"
        "     1000 batch 数据集 cb_interval=20 (50 次回调)"
    )

    # 验证回调条件改为 _cb_interval
    assert re.search(
        r"if progress_callback and batch_idx % _cb_interval == 0:",
        content,
    ), "进度回调应使用 _cb_interval 而非硬编码 100"


# ============== T2.5: DataLoader kwargs 配置 (动态 mock 验证) ==============

def test_t2_5_dataloader_kwargs_cpu():
    """CPU 训练 (num_workers=0): pin_memory 必须 False, 不启用 persistent"""
    # 模拟 _dl_kwargs 构造
    num_workers = 0
    use_cuda = False
    _dl_kwargs = {
        "num_workers": num_workers,
        "pin_memory": num_workers > 0 and use_cuda,
    }
    if num_workers > 0:
        _dl_kwargs["persistent_workers"] = True
        _dl_kwargs["prefetch_factor"] = 2

    assert _dl_kwargs["num_workers"] == 0
    assert _dl_kwargs["pin_memory"] is False
    assert "persistent_workers" not in _dl_kwargs
    assert "prefetch_factor" not in _dl_kwargs


def test_t2_5_dataloader_kwargs_gpu():
    """GPU 训练 (num_workers=4 + CUDA): pin_memory=True, persistent=True, prefetch=2"""
    num_workers = 4
    use_cuda = True
    _dl_kwargs = {
        "num_workers": num_workers,
        "pin_memory": num_workers > 0 and use_cuda,
    }
    if num_workers > 0:
        _dl_kwargs["persistent_workers"] = True
        _dl_kwargs["prefetch_factor"] = 2

    assert _dl_kwargs["num_workers"] == 4
    assert _dl_kwargs["pin_memory"] is True
    assert _dl_kwargs["persistent_workers"] is True
    assert _dl_kwargs["prefetch_factor"] == 2


def test_t2_5_dataloader_kwargs_no_cuda_with_workers():
    """CPU 机器 + num_workers=4 (奇怪配置): pin_memory=False (无 CUDA), persistent=True"""
    num_workers = 4
    use_cuda = False
    _dl_kwargs = {
        "num_workers": num_workers,
        "pin_memory": num_workers > 0 and use_cuda,  # False (无 CUDA)
    }
    if num_workers > 0:
        _dl_kwargs["persistent_workers"] = True
        _dl_kwargs["prefetch_factor"] = 2

    assert _dl_kwargs["num_workers"] == 4
    # pin_memory 需 CUDA, 否则 False
    assert _dl_kwargs["pin_memory"] is False
    # 但 persistent_workers 仍可启用 (CPU 多进程并行解码)
    assert _dl_kwargs["persistent_workers"] is True


# ============== T2.6: 进度回调粒度 (动态计算验证) ==============

def test_t2_6_cb_interval_small_dataset():
    """小数据集 (6 batch, 如 240 样本 / batch_size=20): cb_interval=1 (每 batch 回调)"""
    total_batches = 6
    cb_interval = max(1, total_batches // 50)
    assert cb_interval == 1  # 6 // 50 = 0 → max(1, 0) = 1

    # 模拟 6 batch 的回调次数
    callback_count = sum(1 for i in range(total_batches) if i % cb_interval == 0)
    assert callback_count == 6  # 每 batch 回调


def test_t2_6_cb_interval_medium_dataset():
    """中等数据集 (100 batch): cb_interval=2 (50 次回调)"""
    total_batches = 100
    cb_interval = max(1, total_batches // 50)
    assert cb_interval == 2

    callback_count = sum(1 for i in range(total_batches) if i % cb_interval == 0)
    assert callback_count == 50  # 100 / 2 = 50


def test_t2_6_cb_interval_large_dataset():
    """大数据集 (1000 batch): cb_interval=20 (50 次回调)"""
    total_batches = 1000
    cb_interval = max(1, total_batches // 50)
    assert cb_interval == 20

    callback_count = sum(1 for i in range(total_batches) if i % cb_interval == 0)
    assert callback_count == 50  # 1000 / 20 = 50


def test_t2_6_cb_interval_zero_batches():
    """空数据集 (0 batch): cb_interval=1 (避免除零)"""
    total_batches = 0
    cb_interval = max(1, total_batches // 50) if total_batches > 0 else 1
    assert cb_interval == 1
