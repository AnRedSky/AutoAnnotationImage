"""
Detection Demo Data Preparation (v2.0.0 S3.3)
==============================================

为论文 demo 准备目标检测的合成数据集, 模拟"目标检测入门 demo":
- 3 类: red / green / blue
- 每类生成 30 张 64x64 PNG, 中心画一个对应颜色的小色块 (即"目标"位置)
- 划分: 24 train + 6 val, 写入 demo/data/detection_demo/{images,labels}

约定:
- 输出格式: YOLO 归一化中心点 + 宽高 (cx cy w h), 与 S3.1 yolo_dataset 一致
- 不依赖 ultralytics / 网络, 完全离线合成
- 论文 demo 跑通即可, 不追求真实场景

用法:
    python scripts/prepare_detection_demo.py
    # 输出:
    #   demo/data/detection_demo/images/train/{red,green,blue}/xxx.png
    #   demo/data/detection_demo/images/val/{red,green,blue}/xxx.png
    #   demo/data/detection_demo/labels/{train,val}/xxx.txt
    #   demo/data/detection_demo/data.yaml
"""
import os
import random
import sys
from pathlib import Path
from PIL import Image, ImageDraw


# ============== 参数 ==============

CLASSES = [
    # (name, RGB)
    ("red",   (220, 60, 60)),
    ("green", (60, 200, 90)),
    ("blue",  (50, 90, 220)),
]
PER_CLASS_TOTAL = 30      # 每类 30 张 (论文 demo 跑通即可)
TRAIN_RATIO = 0.8          # 80% 训练 / 20% 校验
IMG_SIZE = 64              # 64x64 (CPU 训练足够快)
BOX_HALF = 12              # 目标色块边长 24x24 (W = 0.375 归一化)


# ============== 工具 ==============

def _class_index(name: str) -> int:
    for i, (n, _) in enumerate(CLASSES):
        if n == name:
            return i
    raise KeyError(name)


def _make_image_with_box(class_rgb, seed: int) -> tuple:
    """生成一张 (Image, bbox 中心点 像素坐标)

    - 背景: 浅灰 (200, 200, 200)
    - 中心画一个 24x24 矩形, 颜色 = class_rgb
    - 颜色微抖动 (random 5%) 避免 100% 撞色被去重
    """
    rng = random.Random(seed)
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), color=(200, 200, 200))
    draw = ImageDraw.Draw(img)
    # 中心点允许 ±6 像素偏移, 避免完全规则
    cx = IMG_SIZE // 2 + rng.randint(-6, 6)
    cy = IMG_SIZE // 2 + rng.randint(-6, 6)
    x1, y1 = cx - BOX_HALF, cy - BOX_HALF
    x2, y2 = cx + BOX_HALF, cy + BOX_HALF
    jitter = lambda v: max(0, min(255, v + rng.randint(-10, 10)))
    color = tuple(jitter(c) for c in class_rgb)
    draw.rectangle([x1, y1, x2, y2], fill=color)
    # 返回 (img, 像素 bbox (x_min, y_min, x_max, y_max))
    return img, (x1, y1, x2, y2)


def _to_yolo_line(pixel_box, class_idx: int) -> str:
    """像素 bbox → YOLO txt 格式: class cx cy w h (归一化 0-1)"""
    x1, y1, x2, y2 = pixel_box
    cx = (x1 + x2) / 2.0 / IMG_SIZE
    cy = (y1 + y2) / 2.0 / IMG_SIZE
    w = (x2 - x1) / IMG_SIZE
    h = (y2 - y1) / IMG_SIZE
    return f"{class_idx} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


# ============== 主流程 ==============

def main():
    root = Path("demo/data/detection_demo")
    img_root = root / "images"
    lbl_root = root / "labels"
    for split in ("train", "val"):
        for cls_name, _ in CLASSES:
            (img_root / split / cls_name).mkdir(parents=True, exist_ok=True)
        (lbl_root / split).mkdir(parents=True, exist_ok=True)

    rng = random.Random(42)
    n_train = int(PER_CLASS_TOTAL * TRAIN_RATIO)

    print(f"[INFO] 生成检测 demo: {len(CLASSES)} 类 × {PER_CLASS_TOTAL} 张, "
          f"train={n_train} val={PER_CLASS_TOTAL - n_train}")

    total_train, total_val = 0, 0
    for cls_idx, (cls_name, cls_rgb) in enumerate(CLASSES):
        for i in range(PER_CLASS_TOTAL):
            split = "train" if i < n_train else "val"
            seed = rng.randint(0, 2**31 - 1)
            img, pixel_box = _make_image_with_box(cls_rgb, seed)
            fname = f"{cls_name}_{i:03d}.png"
            img.save(img_root / split / cls_name / fname, format="PNG")
            yolo_line = _to_yolo_line(pixel_box, cls_idx)
            (lbl_root / split / f"{cls_name}_{i:03d}.txt").write_text(
                yolo_line + "\n", encoding="utf-8"
            )
            if split == "train":
                total_train += 1
            else:
                total_val += 1

    # data.yaml (ultralytics YOLOv8 标准)
    data_yaml = root / "data.yaml"
    data_yaml.write_text(
        f"path: {root.resolve().as_posix()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"nc: {len(CLASSES)}\n"
        f"names: {[c[0] for c in CLASSES]!r}\n",
        encoding="utf-8",
    )

    print(f"[OK] 输出: {root}")
    print(f"     train: {total_train} 张, val: {total_val} 张")
    print(f"     data.yaml: {data_yaml}")


if __name__ == "__main__":
    main()
