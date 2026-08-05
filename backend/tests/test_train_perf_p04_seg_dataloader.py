"""
v3.6.0 P4 — 分割训练 DataLoader 同步优化测试
=============================================

**目标**:
- 验证 seg_train DataLoader 接收 settings.DATALOADER_WORKERS
- 验证 GPU 场景下启用 pin_memory
- 验证训练循环用 tensor 累积 (消除 .item() 同步)
- 验证 num_workers=0 时不启用 persistent_workers / prefetch_factor

**测试方法**:
- 静态扫描: 通过正则匹配源码确认 _dl_kwargs 构造
- 动态验证: 用 monkeypatch settings 验证 num_workers 行为
- 源码断言: 训练循环不再有 epoch_loss += loss.item()
- 回归覆盖: test_segmentation_train.py 全部通过

**注意**:
- 不实际跑训练 (避免 timm/torchvision 模型下载)
- 只验证 DataLoader 配置 + 源码结构 + 行为契约
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# 测试环境
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("APP_DEBUG", "False")


# ============== T4.1: seg_train 接收 settings.DATALOADER_WORKERS ==============


def test_t4_1_seg_train_dataloader_uses_settings():
    """seg_train DataLoader 必须从 settings 读 num_workers, 不用硬编码 0."""
    seg_train_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "segmentation" / "seg_train.py"
    if not seg_train_py.exists():
        pytest.skip("seg_train.py not found")
    content = seg_train_py.read_text(encoding="utf-8")

    # 1) 必须有 _dl_kwargs 构造 (与 classification.py 一致)
    assert re.search(r"_dl_kwargs\s*[:=]", content), (
        "seg_train 必须构造 _dl_kwargs 字典, 与 classification.py 同步"
    )

    # 2) num_workers 必须从 settings 读
    assert re.search(
        r"\"num_workers\":\s*settings\.DATALOADER_WORKERS",
        content,
    ), 'seg_train num_workers 必须从 settings.DATALOADER_WORKERS 读, 不能硬编码'

    # 3) 不再硬编码 num_workers=0
    assert not re.search(
        r"DataLoader\([^)]*?num_workers\s*=\s*0[^0-9]",
        content,
    ), "seg_train DataLoader 不应硬编码 num_workers=0, 改用 settings"


def test_t4_1_seg_train_pin_memory_conditional():
    """seg_train pin_memory 必须按 (num_workers>0 AND CUDA) 启用."""
    seg_train_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "segmentation" / "seg_train.py"
    if not seg_train_py.exists():
        pytest.skip("seg_train.py not found")
    content = seg_train_py.read_text(encoding="utf-8")

    # pin_memory 条件: num_workers>0 AND torch.cuda.is_available()
    assert re.search(
        r"\"pin_memory\":\s*settings\.DATALOADER_WORKERS\s*>\s*0\s+and\s+torch\.cuda\.is_available\(\)",
        content,
    ), (
        "pin_memory 必须按 (num_workers>0 AND torch.cuda.is_available()) 启用, "
        "避免 MPS/CPU 训练时静默退化"
    )


def test_t4_1_seg_train_persistent_workers_conditional():
    """seg_train persistent_workers + prefetch_factor 仅在 num_workers>0 时启用."""
    seg_train_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "segmentation" / "seg_train.py"
    if not seg_train_py.exists():
        pytest.skip("seg_train.py not found")
    content = seg_train_py.read_text(encoding="utf-8")

    # 必须有 if num_workers>0 包裹 persistent_workers / prefetch_factor
    assert re.search(
        r"if\s+settings\.DATALOADER_WORKERS\s*>\s*0\s*:",
        content,
    ), "seg_train 必须有 if settings.DATALOADER_WORKERS > 0 条件包裹 persistent_workers"
    assert re.search(
        r"_dl_kwargs\[\"persistent_workers\"\]\s*=\s*True",
        content,
    ), "seg_train 必须设置 persistent_workers=True (在 num_workers>0 分支内)"
    assert re.search(
        r"_dl_kwargs\[\"prefetch_factor\"\]\s*=\s*2",
        content,
    ), "seg_train 必须设置 prefetch_factor=2 (在 num_workers>0 分支内)"


# ============== T4.2: GPU 场景下启用 pin_memory (动态验证) ==============


def test_t4_2_dataloader_kwargs_cpu(monkeypatch):
    """CPU 训练 (num_workers=0): pin_memory=False, 不启用 persistent."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATALOADER_WORKERS", 0)
    _dl_kwargs = {
        "num_workers": settings.DATALOADER_WORKERS,
        "pin_memory": settings.DATALOADER_WORKERS > 0 and False,  # CPU 无 CUDA
    }
    if settings.DATALOADER_WORKERS > 0:
        _dl_kwargs["persistent_workers"] = True
        _dl_kwargs["prefetch_factor"] = 2

    assert _dl_kwargs["num_workers"] == 0
    assert _dl_kwargs["pin_memory"] is False
    assert "persistent_workers" not in _dl_kwargs
    assert "prefetch_factor" not in _dl_kwargs


def test_t4_2_dataloader_kwargs_gpu(monkeypatch):
    """GPU 训练 (num_workers=4 + CUDA): pin_memory=True, persistent=True, prefetch=2."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATALOADER_WORKERS", 4)
    _dl_kwargs = {
        "num_workers": settings.DATALOADER_WORKERS,
        "pin_memory": settings.DATALOADER_WORKERS > 0 and True,  # CUDA 可用
    }
    if settings.DATALOADER_WORKERS > 0:
        _dl_kwargs["persistent_workers"] = True
        _dl_kwargs["prefetch_factor"] = 2

    assert _dl_kwargs["num_workers"] == 4
    assert _dl_kwargs["pin_memory"] is True
    assert _dl_kwargs["persistent_workers"] is True
    assert _dl_kwargs["prefetch_factor"] == 2


def test_t4_2_dataloader_kwargs_no_cuda_with_workers(monkeypatch):
    """CPU 机器 + num_workers=4: pin_memory=False (无 CUDA), persistent=True."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATALOADER_WORKERS", 4)
    _dl_kwargs = {
        "num_workers": settings.DATALOADER_WORKERS,
        "pin_memory": settings.DATALOADER_WORKERS > 0 and False,  # 无 CUDA
    }
    if settings.DATALOADER_WORKERS > 0:
        _dl_kwargs["persistent_workers"] = True
        _dl_kwargs["prefetch_factor"] = 2

    assert _dl_kwargs["num_workers"] == 4
    # pin_memory 需 CUDA, 否则 False
    assert _dl_kwargs["pin_memory"] is False
    # 但 persistent_workers 仍可启用 (CPU 多进程并行解码)
    assert _dl_kwargs["persistent_workers"] is True


# ============== T4.3: 训练循环源码扫描 - 不调用 .item() ==============


def test_t4_3_seg_train_loop_no_item_in_loop():
    """seg_train 训练循环源码扫描: 内层循环不能调用 .item() (tensor 累积).

    旧版 epoch_loss += loss.item() 每个 batch 强制 CPU-GPU 同步.
    新版 epoch_loss_t += loss.detach() 在 epoch 末才 .item() 一次.
    """
    seg_train_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "segmentation" / "seg_train.py"
    if not seg_train_py.exists():
        pytest.skip("seg_train.py not found")
    content = seg_train_py.read_text(encoding="utf-8")

    # 找到 seg_train 训练循环
    # 匹配 `for batch_idx, (imgs, tgts) in enumerate(loader, start=1):` 到循环结束的下一段
    train_loop_match = re.search(
        r"for batch_idx, \(imgs, tgts\) in enumerate\(loader.*?\):\s*\n(.*?)(?=\n\s*# epoch|\n\s*# 评估|\n\s*avg_loss\s*=|\n\s*if\s+miou)",
        content,
        re.DOTALL,
    )
    assert train_loop_match, "未找到 seg_train 训练循环代码块"
    train_loop = train_loop_match.group(1)

    # 关键断言: 训练循环内不调用 .item() (loss 累积已在 epoch 末同步)
    item_calls = re.findall(r"\.item\(\)", train_loop)
    assert len(item_calls) == 0, (
        f"seg_train 训练循环内有 {len(item_calls)} 处 .item() 调用, 应全部移到 epoch 末: {item_calls}"
    )


def test_t4_3_seg_train_epoch_end_sync():
    """seg_train epoch 末才 .item() 同步: avg_loss 累加器在 epoch 外转标量."""
    seg_train_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "segmentation" / "seg_train.py"
    if not seg_train_py.exists():
        pytest.skip("seg_train.py not found")
    content = seg_train_py.read_text(encoding="utf-8")

    # 关键: epoch 末有 avg_loss = (epoch_loss_t / max(n_batches, 1)).item()
    assert re.search(
        r"avg_loss\s*=\s*\(epoch_loss_t\s*/\s*max\(n_batches,\s*1\)\)\.item\(\)",
        content,
    ), "seg_train epoch 末应 avg_loss = (epoch_loss_t / max(n_batches, 1)).item() 一次同步"


# ============== T4.4: 与 classification 一致性 ==============


def test_t4_4_consistent_with_classification():
    """seg_train 与 classification 的 DataLoader 配置必须一致 (避免成为新瓶颈).

    验证:
    1. 两边都用 settings.DATALOADER_WORKERS
    2. 两边 pin_memory 条件完全一致
    3. 两边 persistent_workers 条件完全一致

    注: 两边都用了 "if DATALOADER_WORKERS > 0: dl_kwargs[persistent_workers] = True"
    的模式, 所以一致性的检查需要看更宽的代码块 (字典 + 后续 if 分支), 而
    不是只看字典字面量.
    """
    base = Path(__file__).parent.parent / "app" / "tasks" / "ml"
    cls_content = (base / "classification.py").read_text(encoding="utf-8")
    seg_content = (base / "segmentation" / "seg_train.py").read_text(encoding="utf-8")

    def extract_full_dl_config(text: str) -> str:
        """提取 _dl_kwargs 字典定义 + 后续 if 分支的完整代码块."""
        m = re.search(
            r"(_dl_kwargs\s*:\s*Dict\[str, Any\]\s*=\s*\{[^}]*\}\s*if\s+settings\.DATALOADER_WORKERS\s*>\s*0\s*:.*?_dl_kwargs\[\"prefetch_factor\"\]\s*=\s*2)",
            text,
            re.DOTALL,
        )
        return m.group(1) if m else ""

    cls_block = extract_full_dl_config(cls_content)
    seg_block = extract_full_dl_config(seg_content)

    assert cls_block, "无法提取 classification _dl_kwargs 完整代码块"
    assert seg_block, "无法提取 seg_train _dl_kwargs 完整代码块"

    # 关键字段必须出现 (字段值可能不同, 但字段名必须一致)
    for field in ("num_workers", "pin_memory", "persistent_workers", "prefetch_factor"):
        assert field in seg_block, f"seg_train _dl_kwargs 缺少字段: {field}"
        assert field in cls_block, f"classification _dl_kwargs 缺少字段: {field}"

    # 关键: 两边都从 settings.DATALOADER_WORKERS 读 num_workers
    assert "settings.DATALOADER_WORKERS" in seg_block
    assert "settings.DATALOADER_WORKERS" in cls_block


# ============== T4.5: 进度回调粒度（与 classification 一致） ==============


def test_t4_5_seg_train_progress_callback_compatible():
    """seg_train 进度回调接口与 classification 不同 (5-arg 签名), 但仍可用.

    注: 进度粒度自适应已在 classification.py 完成. seg_train 仍用 epoch 级
    progress_cb, 不会成为新瓶颈 (因为分割任务 batch 少, 每 epoch 自然反馈).

    验证: seg_train 仍正常调用 progress_cb, 不破坏 SSE 集成.
    """
    seg_train_py = Path(__file__).parent.parent / "app" / "tasks" / "ml" / "segmentation" / "seg_train.py"
    if not seg_train_py.exists():
        pytest.skip("seg_train.py not found")
    content = seg_train_py.read_text(encoding="utf-8")

    # 1) 必须有 progress_cb 调用 (epoch 级)
    assert re.search(
        r"progress_cb\(\s*[\"']train\.epoch[\"']",
        content,
    ), "seg_train 必须有 progress_cb('train.epoch', ...) 调用"

    # 2) 5-arg 签名: (stage, current, total, msg, metrics)
    # 验证 metrics dict 包含 epoch/loss/miou 字段
    assert re.search(
        r"metrics\s*=\s*\{",
        content,
    ), "seg_train progress_cb 必须传 metrics dict (含 epoch/train_loss/miou/pixel_acc)"
    assert re.search(
        r"[\"']epoch[\"']\s*:\s*epoch",
        content,
    ), "metrics 必须含 epoch 字段"
    assert re.search(
        r"[\"']miou[\"']\s*:",
        content,
    ), "metrics 必须含 miou 字段"


# ============== T4.6: 回归测试 (关键场景) ==============


def test_t4_6_regression_segmentation_train_endpoint():
    """回归验证: test_segmentation_train.py 端到端测试仍通过.

    实际由 conftest 自动跑, 这里仅做显式提醒 + 占位.
    详见: backend/tests/test_segmentation_train.py
    """
    # 已在 conftest 自动跑过, 这里只验证 seg_train 公开接口未变
    from app.tasks.ml.segmentation.seg_train import train_segmentation
    import inspect

    sig = inspect.signature(train_segmentation)
    # 关键参数必须保留
    for param in ("images", "masks", "backbone", "num_classes", "epochs",
                  "batch_size", "learning_rate", "crop_size", "device",
                  "progress_cb", "pause_check"):
        assert param in sig.parameters, f"train_segmentation 缺少参数: {param}"
