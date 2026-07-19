"""
BBox 几何计算服务 (v2.0.0 目标检测)
====================================

职责:
- 归一化坐标 ↔ 像素坐标 互转
- IoU (Intersection over Union) 计算
- NMS (Non-Maximum Suppression) 抑制
- 坐标合法性校验

约定:
- 数据库存储: 归一化坐标 0-1 (与 YOLO txt 一致, 避免坐标系转换)
- 输入输出: 统一用 dataclass / dict, 字段名 x_min/y_min/x_max/y_max
- 全部方法都是纯函数 (无状态、无 IO), 便于单测

设计原则:
- 单一职责: 本文件只做几何/数学运算, 不查 DB、不写 DB
- API 层 (app/api/detection.py) 负责 IO 编排
- 算法可独立复用 (后续 YOLO 推理、训练数据增强 也会用)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple


# ============== 坐标表示 ==============

@dataclass
class BBox:
    """单个边界框 (归一化坐标 0-1)

    字段:
    - x_min / y_min / x_max / y_max: 归一化 0-1
    - confidence: AI 推理置信度 (人工为 None)
    - category_id: 类别 id (None 表示未指定)
    - label: 类别名 (可选, 仅用于显示/调试, 不参与几何)
    """
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    confidence: float | None = None
    category_id: int | None = None
    label: str | None = None

    def width(self) -> float:
        return max(0.0, self.x_max - self.x_min)

    def height(self) -> float:
        return max(0.0, self.y_max - self.y_min)

    def area(self) -> float:
        return self.width() * self.height()

    def to_dict(self) -> dict:
        return {
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
            "confidence": self.confidence,
            "category_id": self.category_id,
            "label": self.label,
        }


# ============== 校验 ==============

def validate_normalized_bbox(
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
) -> None:
    """校验归一化坐标合法性

    Raises:
        ValueError: 坐标越界 / 宽高为 0
    """
    for name, v in (("x_min", x_min), ("y_min", y_min),
                    ("x_max", x_max), ("y_max", y_max)):
        if v is None:
            raise ValueError(f"{name} is None")
        if not (0.0 <= v <= 1.0):
            raise ValueError(
                f"{name}={v} 超出归一化范围 [0, 1]"
            )
    if x_max <= x_min:
        raise ValueError(
            f"x_max ({x_max}) 必须严格大于 x_min ({x_min})"
        )
    if y_max <= y_min:
        raise ValueError(
            f"y_max ({y_max}) 必须严格大于 y_min ({y_min})"
        )


# ============== 坐标转换 ==============

def normalized_to_pixels(
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    img_width: int,
    img_height: int,
) -> Tuple[int, int, int, int]:
    """归一化坐标 → 像素坐标 (左上右下)

    物理像素 = 归一化 × 图像尺寸
    取整策略: 像素坐标用 round() (四舍五入到最近像素),
    避免左开右闭 vs 左闭右开的歧义
    """
    validate_normalized_bbox(x_min, y_min, x_max, y_max)
    if img_width <= 0 or img_height <= 0:
        raise ValueError(
            f"图像尺寸非法: {img_width}x{img_height}"
        )
    return (
        round(x_min * img_width),
        round(y_min * img_height),
        round(x_max * img_width),
        round(y_max * img_height),
    )


def pixels_to_normalized(
    x_min: int,
    y_min: int,
    x_max: int,
    y_max: int,
    img_width: int,
    img_height: int,
) -> Tuple[float, float, float, float]:
    """像素坐标 → 归一化坐标

    用于: 前端 canvas 拿到的像素坐标, 入库前归一化
    取值: 像素 / 尺寸, 浮点保留
    """
    if img_width <= 0 or img_height <= 0:
        raise ValueError(
            f"图像尺寸非法: {img_width}x{img_height}"
        )
    return (
        x_min / img_width,
        y_min / img_height,
        x_max / img_width,
        y_max / img_height,
    )


# ============== IoU ==============

def iou(a: BBox, b: BBox) -> float:
    """计算两个 bbox 的 IoU (Intersection over Union)

    IoU = inter / union
    - 无交集 → 0.0
    - 完全重合 → 1.0
    - 部分相交 → (0, 1)
    """
    # 相交矩形
    inter_x_min = max(a.x_min, b.x_min)
    inter_y_min = max(a.y_min, b.y_min)
    inter_x_max = min(a.x_max, b.x_max)
    inter_y_max = min(a.y_max, b.y_max)

    inter_w = max(0.0, inter_x_max - inter_x_min)
    inter_h = max(0.0, inter_y_max - inter_y_min)
    inter_area = inter_w * inter_h

    union_area = a.area() + b.area() - inter_area
    if union_area <= 0.0:
        return 0.0
    return inter_area / union_area


# ============== NMS ==============

def nms(
    boxes: Sequence[BBox],
    iou_threshold: float = 0.5,
) -> List[BBox]:
    """非极大值抑制 (Non-Maximum Suppression)

    经典算法:
    1. 按 confidence 降序
    2. 取最高分 box 加入保留列表
    3. 移除与保留 box IoU > threshold 的所有 box
    4. 重复 2-3 直到为空

    Args:
        boxes: 待筛选的 bbox 列表 (允许 confidence 为 None, 视为 0)
        iou_threshold: IoU 阈值, 高于此值的相邻框被抑制

    Returns:
        保留的 bbox 列表 (按 confidence 降序)
    """
    if not boxes:
        return []

    # 1) confidence 降序; None 视为 0, 排到末尾
    sorted_boxes = sorted(
        boxes,
        key=lambda b: (b.confidence is None, -(b.confidence or 0.0)),
    )

    kept: List[BBox] = []
    while sorted_boxes:
        best = sorted_boxes.pop(0)
        kept.append(best)
        remaining: List[BBox] = []
        for other in sorted_boxes:
            if iou(best, other) <= iou_threshold:
                remaining.append(other)
        sorted_boxes = remaining

    return kept


def class_wise_nms(
    boxes: Sequence[BBox],
    iou_threshold: float = 0.5,
) -> List[BBox]:
    """类别感知 NMS (同类别才互相抑制, 跨类别不抑制)

    YOLOv8 默认行为:
    - cat A 的 box 不会抑制 cat B 的 box
    - 防止「猫」和「狗」互相竞争同一个目标

    Args:
        boxes: 待筛选的 bbox 列表
        iou_threshold: IoU 阈值

    Returns:
        保留的 bbox 列表
    """
    if not boxes:
        return []

    # 按 category_id 分组
    by_category: dict = {}
    for b in boxes:
        # 无 category_id 的归入同一组 (-1) 走通用 NMS
        key = b.category_id if b.category_id is not None else -1
        by_category.setdefault(key, []).append(b)

    kept: List[BBox] = []
    for _, group in by_category.items():
        kept.extend(nms(group, iou_threshold=iou_threshold))
    return kept


# ============== 格式转换 ==============

def bbox_from_dict(d: dict) -> BBox:
    """从 ORM / Pydantic dict 构造 BBox dataclass

    容错: 缺 confidence / category_id / label 时填 None
    """
    return BBox(
        x_min=float(d["x_min"]),
        y_min=float(d["y_min"]),
        x_max=float(d["x_max"]),
        y_max=float(d["y_max"]),
        confidence=d.get("confidence"),
        category_id=d.get("category_id"),
        label=d.get("label"),
    )


def bbox_to_yolo_line(b: BBox, class_index: int) -> str:
    """转换为 YOLO txt 格式: class x_center y_center w h

    约定: YOLO 用中心点 + 宽高 (归一化)
    """
    cx = (b.x_min + b.x_max) / 2.0
    cy = (b.y_min + b.y_max) / 2.0
    w = b.width()
    h = b.height()
    return f"{class_index} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def yolo_line_to_bbox(line: str, num_classes: int = 80) -> BBox:
    """YOLO txt 行 → BBox

    Args:
        line: 一行 "class cx cy w h"
        num_classes: 仅用于占位 (此处不强制范围校验, 由调用方决定)
    """
    parts = line.strip().split()
    if len(parts) < 5:
        raise ValueError(f"YOLO line 字段不足 (期望 5, 实际 {len(parts)}): {line!r}")
    _ = num_classes  # 保留参数, 未来可加 class_index 范围校验
    _cls = int(float(parts[0]))
    cx, cy, w, h = (float(x) for x in parts[1:5])
    x_min = cx - w / 2.0
    y_min = cy - h / 2.0
    x_max = cx + w / 2.0
    y_max = cy + h / 2.0
    return BBox(
        x_min=x_min, y_min=y_min,
        x_max=x_max, y_max=y_max,
        confidence=None,
        category_id=_cls,
    )
