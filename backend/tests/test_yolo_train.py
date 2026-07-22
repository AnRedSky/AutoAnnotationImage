"""
v2.5.15 P1-7 / D-1 测试: YOLO 训练模块
========================================
覆盖 4 条用例, mock ultralytics.YOLO 避免实际训练:

 1. test_train_yolo_missing_data_yaml  data.yaml 不存在抛 YoloTrainError
 2. test_train_yolo_invalid_epochs      epochs < 1 抛 YoloTrainError
 3. test_train_yolo_mock_ultralytics    mock YOLO.train 返回 best.pt
 4. test_cleanup_old_runs               保留最近 N 个 run, 清理老的
"""
import io
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.ml.detection.yolo_train import (
    YoloTrainError,
    train_yolo,
    cleanup_old_runs,
)


# ============== 1. 缺 data.yaml ==============

def test_train_yolo_missing_data_yaml(tmp_path):
    """data.yaml 不存在 -> YoloTrainError"""
    with pytest.raises(YoloTrainError, match="data.yaml 不存在"):
        train_yolo(
            data_yaml=str(tmp_path / "missing.yaml"),
            epochs=1, project=str(tmp_path / "runs"),
        )


# ============== 2. epochs 非法 ==============

def test_train_yolo_invalid_epochs(tmp_path):
    """epochs < 1 -> YoloTrainError"""
    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text("path: /tmp\n")
    with pytest.raises(YoloTrainError, match="epochs 必须 >= 1"):
        train_yolo(
            data_yaml=str(data_yaml),
            epochs=0, project=str(tmp_path / "runs"),
        )


# ============== 3. mock YOLO.train ==============

def test_train_yolo_mock_ultralytics(tmp_path):
    """mock YOLO.train, 验证返回结构 + 进度回调被调"""
    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text("path: /tmp\n")
    run_dir = tmp_path / "runs" / "detect_train"
    weights_dir = run_dir / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    best_pt = weights_dir / "best.pt"
    best_pt.write_bytes(b"fake_best")
    last_pt = weights_dir / "last.pt"
    last_pt.write_bytes(b"fake_last")

    # 进度回调捕获
    progress_calls: list = []

    def _progress(stage, current, total, info):
        progress_calls.append((stage, current, total, info))

    with patch("ultralytics.YOLO") as MockYOLO:
        # 模拟 results.box 属性
        mock_results = MagicMock()
        mock_results.box.map50 = 0.85
        mock_results.box.map = 0.72
        mock_results.box.mp = 0.80
        mock_results.box.mr = 0.75
        MockYOLO.return_value.train.return_value = mock_results

        out = train_yolo(
            data_yaml=str(data_yaml),
            model_name="yolov8n.pt",
            epochs=3, project=str(tmp_path / "runs"),
            progress_cb=_progress,
        )

    # 返回结构验证
    assert out["best_pt"].endswith("best.pt")
    assert out["last_pt"].endswith("last.pt")
    assert out["epochs_trained"] == 3
    assert "duration_seconds" in out
    assert "run_dir" in out
    assert out["metrics"]["map_50"] == pytest.approx(0.85, abs=1e-4)

    # 进度回调被调用: mock 训练不触发 epoch 回调, 只有 train.done 1 次
    # 真实训练下还会有多个 train.epoch
    assert len(progress_calls) >= 1
    stages = [c[0] for c in progress_calls]
    assert "train.done" in stages


# ============== 4. cleanup_old_runs ==============

def test_cleanup_old_runs(tmp_path, monkeypatch):
    """保留最近 N 个 run, 清理老的"""
    # monkeypatch 路径到 tmp_path
    base = tmp_path / "backend" / "models" / "runs"
    base.mkdir(parents=True, exist_ok=True)

    # 模拟 5 个 run 目录, mtime 不同
    import time
    runs = []
    for i in range(5):
        d = base / f"run_{i}"
        d.mkdir()
        (d / "weights").mkdir()
        # mtime 递增 (run_4 最新)
        mt = time.time() - (5 - i) * 100
        import os
        os.utime(d, (mt, mt))
        runs.append(d)

    # monkeypatch 替换 base 路径
    monkeypatch.chdir(tmp_path)
    removed = cleanup_old_runs(keep_last=2)
    assert removed == 3  # 清理 3 个老的
    # 剩下 2 个最新的
    remaining = sorted([d.name for d in base.iterdir() if d.is_dir()])
    assert len(remaining) == 2
    assert "run_3" in remaining
    assert "run_4" in remaining
