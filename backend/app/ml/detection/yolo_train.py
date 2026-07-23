"""
YOLO Training Adapter (v2.0.0 目标检测)
=========================================

职责:
- 调用 ultralytics YOLO (YOLOv8n 起步, 可换 yolov8s/m/l/x)
- 解析训练过程 (epoch / loss / mAP) → 回调
- 训练结束产出 best.pt / last.pt, 同步给 ModelVersion

设计:
- ultralytics 必须懒加载 (5xx MB) → 仅 _run_yolo_train_sync 内 import
- 进度回调: (stage, current_epoch, total_epochs, metrics_dict)
  - metrics_dict 含 train/val 的 box_loss / cls_loss / dfl_loss / mAP50 / mAP50-95
- CPU 友好: 论文 demo 跑 CPU 即可 (epochs 5-10, imgsz 320, 几十张图)
- 失败抛出 YoloTrainError, 由 Celery 任务捕获并写 DB error_msg
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


ProgressCallback = Optional[Callable[[str, int, int, Dict[str, Any]], None]]


class YoloTrainError(RuntimeError):
    """YOLO 训练失败包装异常, 由 Celery 任务捕获并写 TrainingJob.error_msg"""
    pass


# ============== 训练主入口 ==============

def train_yolo(
    data_yaml: str,
    model_name: str = "yolov8n.pt",
    epochs: int = 10,
    imgsz: int = 320,
    batch: int = 8,
    device: str = "cpu",
    project: Optional[str] = None,
    name: str = "detect_train",
    progress_cb: ProgressCallback = None,
) -> Dict[str, Any]:
    """
    同步训练 YOLOv8 (在 Celery worker 线程池中调用, 不阻塞 event loop)

    Args:
        data_yaml: yolo_dataset.export_yolo_dataset 产出的 data.yaml 路径
        model_name: ultralytics 预训练权重名 (yolov8n/s/m/l/x, 也可本地 .pt)
        epochs: 训练轮数
        imgsz: 输入尺寸
        batch: 批大小
        device: "cpu" / "cuda" / "0" (cuda:0)
        project: ultralytics 训练产物根目录
        name: 实验名 (run 名)
        progress_cb: 进度回调

    Returns:
        dict {
            "best_pt": str,      # 最佳权重绝对路径
            "last_pt": str,
            "metrics": {         # 最终 epoch 指标
                "map_50": float,
                "map_50_95": float,
                "precision": float,
                "recall": float,
                "box_loss": float,
                "cls_loss": float,
                "dfl_loss": float,
            },
            "epochs_trained": int,
            "duration_seconds": float,
            "run_dir": str,      # ultralytics 训练 run 目录
        }

    Raises:
        YoloTrainError: ultralytics 未装 / data_yaml 缺失 / 训练失败
    """
    if not Path(data_yaml).exists():
        raise YoloTrainError(f"data.yaml 不存在: {data_yaml}")
    if epochs < 1:
        raise YoloTrainError(f"epochs 必须 >= 1, 实际 {epochs}")

    if project is None:
        from app.config import settings
        project = str(settings.MODEL_DIR / "runs")

    started = datetime.utcnow()
    run_dir = Path(project) / name
    run_dir.mkdir(parents=True, exist_ok=True)

    # ultralytics + torch 全部 lazy
    try:
        from ultralytics import YOLO  # noqa
    except ImportError as e:
        raise YoloTrainError(
            f"ultralytics 未安装: {e}. 请运行 'pip install ultralytics'"
        ) from e

    try:
        model = YOLO(model_name)
    except Exception as e:
        raise YoloTrainError(f"加载预训练权重失败 ({model_name}): {e}") from e

    # 训练回调: ultralytics 提供 add_callback('on_train_epoch_end', fn)
    def _on_epoch_end(trainer):
        epoch = trainer.epoch + 1  # 0-based → 1-based
        # trainer.tloss 是 list (各 loss 平均), 这里取最后一次 batch 的 loss
        metrics = {
            "box_loss": float(getattr(trainer, "tloss", [0.0])[0])
                if hasattr(trainer, "tloss") and trainer.tloss else 0.0,
            "cls_loss": float(getattr(trainer, "tloss", [0.0, 0.0])[1])
                if hasattr(trainer, "tloss") and len(getattr(trainer, "tloss", [])) > 1
                else 0.0,
            "dfl_loss": float(getattr(trainer, "tloss", [0.0, 0.0, 0.0])[2])
                if hasattr(trainer, "tloss") and len(getattr(trainer, "tloss", [])) > 2
                else 0.0,
        }
        # trainer.metrics 里有验证集结果 (mAP / P / R)
        for k in ("metrics/mAP50(B)", "metrics/mAP50-95(B)",
                  "metrics/precision(B)", "metrics/recall(B)"):
            v = getattr(trainer, k, None) if hasattr(trainer, k) else None
            if v is not None:
                metrics[k.split("/")[-1].replace("(B)", "")
                          .replace("mAP50-95", "map_50_95")
                          .replace("mAP50", "map_50")] = float(v)
        if progress_cb:
            progress_cb("train.epoch", epoch, epochs, metrics)

    try:
        model.add_callback("on_train_epoch_end", _on_epoch_end)
    except Exception as e:
        # 新版 ultralytics 移除 add_callback, 进度降级为无 epoch 回调
        logger.warning("add_callback 不可用, 训练进度回调降级: %s", e)

    # 启动训练 (verbose=False 避免把 log 写满 celery worker stdout)
    try:
        results = model.train(
            data=data_yaml,
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            project=project,
            name=name,
            exist_ok=True,
            verbose=False,
            # 论文 demo: 关闭 mosaic/混合精度避免 CPU 慢
            mosaic=0.0 if device == "cpu" else 1.0,
            amp=False if device == "cpu" else True,
        )
    except Exception as e:
        raise YoloTrainError(f"YOLO 训练失败: {e}") from e

    # 训练完成: 找 best.pt / last.pt
    best_pt = run_dir / "weights" / "best.pt"
    last_pt = run_dir / "weights" / "last.pt"
    if not best_pt.exists():
        raise YoloTrainError(f"训练完成但 best.pt 不存在: {best_pt}")
    if not last_pt.exists():
        last_pt = best_pt  # 兜底

    # 抽最终指标
    final_metrics: Dict[str, Any] = {}
    try:
        # results.box 包含 val 阶段指标
        if hasattr(results, "box"):
            box = results.box
            final_metrics["map_50"] = float(getattr(box, "map50", 0.0) or 0.0)
            final_metrics["map_50_95"] = float(getattr(box, "map", 0.0) or 0.0)
            final_metrics["precision"] = float(getattr(box, "mp", 0.0) or 0.0)
            final_metrics["recall"] = float(getattr(box, "mr", 0.0) or 0.0)
    except Exception as e:
        logger.warning("训练指标抽取失败, 返回空 metrics: %s", e)

    duration = (datetime.utcnow() - started).total_seconds()
    if progress_cb:
        progress_cb("train.done", epochs, epochs, final_metrics)

    return {
        "best_pt": str(best_pt.resolve()),
        "last_pt": str(last_pt.resolve()),
        "metrics": final_metrics,
        "epochs_trained": epochs,
        "duration_seconds": duration,
        "run_dir": str(run_dir.resolve()),
    }


# ============== 训练后清理 ==============

def cleanup_old_runs(keep_last: int = 3) -> int:
    """
    保留最近 N 次训练 run, 清理更早的 (节省磁盘)
    返回被清理的 run 数量
    """
    from app.config import settings
    base = settings.MODEL_DIR / "runs"
    if not base.exists():
        return 0
    runs = sorted(
        [d for d in base.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in runs[keep_last:]:
        try:
            shutil.rmtree(old)
            removed += 1
        except OSError as e:
            logger.warning("清理旧 run 目录失败 %s: %s", old, e)
    return removed
