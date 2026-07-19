"""
YOLO Prediction Adapter (v2.0.0 目标检测)
==========================================

职责:
- 加载已训练好的 YOLOv8 best.pt
- 单图/批量推理, 输出 BBox 列表 (归一化坐标)
- 类别索引 → 原始 Category.id 映射

设计:
- ultralytics 懒加载
- predict_sync 内部 import, 全部在 Celery / FastAPI BackgroundTasks 线程池中跑
- 输出结构与 bbox_service.BBox / BBoxAnnotation ORM 字段对齐, 直接入库
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


# ============== 结果结构 ==============

class YoloBox:
    """单条推理结果 (归一化坐标 0-1)

    - class_index: YOLO 训练时的 0-based 类索引
    - x_min/y_min/x_max/y_max: 归一化
    - confidence: 置信度
    """
    __slots__ = ("class_index", "x_min", "y_min", "x_max", "y_max", "confidence")

    def __init__(self, class_index: int, x_min: float, y_min: float,
                 x_max: float, y_max: float, confidence: float):
        self.class_index = class_index
        self.x_min = x_min
        self.y_min = y_min
        self.x_max = x_max
        self.y_max = y_max
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_index": self.class_index,
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
            "confidence": self.confidence,
        }


# ============== 主入口: 单图/批量预测 ==============

def predict_yolo(
    weights_path: str,
    image_paths: Sequence[str],
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    imgsz: int = 320,
    device: str = "cpu",
) -> List[YoloBox]:
    """
    调用 ultralytics YOLO 推理

    Args:
        weights_path: 训练产出的 best.pt 绝对路径
        image_paths: 待推理图片绝对路径列表
        conf_threshold: 置信度阈值
        iou_threshold: NMS IoU 阈值 (YOLO 内部 NMS)
        imgsz: 推理输入尺寸
        device: "cpu" / "cuda"

    Returns:
        List[YoloBox] (按 image 顺序, 每张图多个框)
        注: 当前实现未按 image 拆分; 调用方通过 image_paths 长度对结果分组

    Raises:
        FileNotFoundError: 权重文件不存在
        RuntimeError: ultralytics 未装 / 推理失败
    """
    if not Path(weights_path).exists():
        raise FileNotFoundError(f"权重文件不存在: {weights_path}")
    if not image_paths:
        return []

    # lazy import ultralytics
    try:
        from ultralytics import YOLO  # noqa
    except ImportError as e:
        raise RuntimeError(
            f"ultralytics 未安装: {e}. 请运行 'pip install ultralytics'"
        ) from e

    try:
        model = YOLO(weights_path)
    except Exception as e:
        raise RuntimeError(f"加载权重失败: {e}") from e

    # ultralytics.predict 支持直接传 list
    try:
        results = model.predict(
            source=list(image_paths),
            conf=conf_threshold,
            iou=iou_threshold,
            imgsz=imgsz,
            device=device,
            verbose=False,
            stream=False,  # 一次性拿结果, 简单
        )
    except Exception as e:
        raise RuntimeError(f"YOLO 推理失败: {e}") from e

    # results 是 list (每张图一个 Results)
    out: List[YoloBox] = []
    for r in results:
        # r.boxes.xyxyn / xyn 是归一化 [x_min, y_min, x_max, y_max]
        # r.boxes.conf 是置信度
        # r.boxes.cls 是 class_index
        if r.boxes is None or len(r.boxes) == 0:
            continue
        xy = r.boxes.xyxyn.cpu().numpy()  # (N, 4) 归一化
        conf = r.boxes.conf.cpu().numpy()
        cls = r.boxes.cls.cpu().numpy().astype(int)
        for i in range(len(xy)):
            x1, y1, x2, y2 = xy[i]
            out.append(YoloBox(
                class_index=int(cls[i]),
                x_min=float(x1), y_min=float(y1),
                x_max=float(x2), y_max=float(y2),
                confidence=float(conf[i]),
            ))
    return out


# ============== 按图分组 ==============

def group_predictions_by_image(
    image_paths: Sequence[str],
    predictions: List[YoloBox],
) -> Dict[str, List[YoloBox]]:
    """
    把 YoloBox 列表按 image 拆开

    Args:
        image_paths: 推理时传入的图片路径列表 (顺序)
        predictions: predict_yolo 返回的扁平行 (按 image 顺序连接)

    Returns:
        {image_path: [YoloBox, ...]}
    """
    grouped: Dict[str, List[YoloBox]] = {p: [] for p in image_paths}
    # ultralytics.predict 的默认顺序与 source 一致;
    # 但当某些图 0 框时, 需要按图数对齐 (每图 results 一次)
    # 此处由调用方保证 predictions 与 image_paths 已对齐 (见 predict_image_grouped)

    return grouped


def predict_image_grouped(
    weights_path: str,
    image_paths: Sequence[str],
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    imgsz: int = 320,
    device: str = "cpu",
) -> Dict[str, List[YoloBox]]:
    """单次调用, 按 image 分组返回结果

    Returns:
        {image_path: [YoloBox, ...]}, 无框的图仍是空 list
    """
    if not Path(weights_path).exists():
        raise FileNotFoundError(f"权重文件不存在: {weights_path}")
    if not image_paths:
        return {}

    try:
        from ultralytics import YOLO  # lazy
    except ImportError as e:
        raise RuntimeError(
            f"ultralytics 未安装: {e}"
        ) from e

    model = YOLO(weights_path)
    results = model.predict(
        source=list(image_paths),
        conf=conf_threshold,
        iou=iou_threshold,
        imgsz=imgsz,
        device=device,
        verbose=False,
    )

    out: Dict[str, List[YoloBox]] = {}
    for path, r in zip(image_paths, results):
        boxes: List[YoloBox] = []
        if r.boxes is not None and len(r.boxes) > 0:
            xy = r.boxes.xyxyn.cpu().numpy()
            conf = r.boxes.conf.cpu().numpy()
            cls = r.boxes.cls.cpu().numpy().astype(int)
            for i in range(len(xy)):
                x1, y1, x2, y2 = xy[i]
                boxes.append(YoloBox(
                    class_index=int(cls[i]),
                    x_min=float(x1), y_min=float(y1),
                    x_max=float(x2), y_max=float(y2),
                    confidence=float(conf[i]),
                ))
        out[path] = boxes
    return out
