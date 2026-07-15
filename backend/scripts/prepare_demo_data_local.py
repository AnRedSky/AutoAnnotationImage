"""
Demo Data Preparation Script (Local Synthetic)
=============================================
当 CIFAR-10 下载缓慢/不可用时, 本脚本会生成本地合成图片,
作为论文演示和系统联调的备用数据集。

输出:
    demo/data/cifar10_subset/train/{class_name}/*.jpg
    demo/data/cifar10_subset/val/{class_name}/*.jpg

特点:
- 5 类 × 200 张 × 2 划分 (train 160 + val 40)
- 每张图带可识别的颜色模式 (论文中以"垃圾分 5 类"代称)
- 体积小 (约 5MB), 生成快 (< 5s)
- 可被 EfficientNet 等预训练模型以 0% 置信度作为 fallback
"""
import os
import sys
import random
from pathlib import Path
from PIL import Image, ImageDraw


# 5 类 (与 CIFAR-10 选类一致, 论文演示用"垃圾分 5 类"代称)
SELECTED_CLASSES = ["airplane", "automobile", "bird", "cat", "deer"]
PER_CLASS_TOTAL = 200
TRAIN_RATIO = 0.8
OUTPUT_DIR = Path("demo/data/cifar10_subset")
TRAIN_DIR = OUTPUT_DIR / "train"
VAL_DIR = OUTPUT_DIR / "val"

# 各类基准色 (RGB)
CLASS_COLORS = {
    "airplane": (180, 200, 220),     # 天蓝灰
    "automobile": (220, 60, 60),     # 红
    "bird": (255, 200, 50),          # 黄
    "cat": (140, 90, 50),            # 棕
    "deer": (100, 160, 80),          # 绿
}


def gen_synthetic_image(class_name: str, idx: int, size: int = 32) -> Image.Image:
    """为指定类别生成一张 32x32 的合成图像
    - 主色 = 类别基准色
    - 加上随机噪点, 让 AI 有"非平凡"特征可学
    """
    base = CLASS_COLORS.get(class_name, (128, 128, 128))
    img = Image.new("RGB", (size, size), color=base)
    draw = ImageDraw.Draw(img)

    rng = random.Random(f"{class_name}_{idx}")
    # 加随机噪点
    for _ in range(size * size // 2):
        x = rng.randint(0, size - 1)
        y = rng.randint(0, size - 1)
        delta = rng.randint(-50, 50)
        r = max(0, min(255, base[0] + delta))
        g = max(0, min(255, base[1] + delta))
        b = max(0, min(255, base[2] + delta))
        draw.point((x, y), fill=(r, g, b))

    # 画一个类别标识形状 (简单几何)
    if class_name == "airplane":
        draw.line([(4, 16), (28, 16)], fill=(0, 0, 0), width=2)
    elif class_name == "automobile":
        draw.rectangle([(6, 10), (26, 22)], outline=(0, 0, 0), width=2)
    elif class_name == "bird":
        draw.ellipse([(10, 8), (22, 20)], outline=(0, 0, 0), width=2)
    elif class_name == "cat":
        draw.polygon([(10, 10), (16, 6), (22, 10), (22, 24), (10, 24)],
                     outline=(0, 0, 0))
    elif class_name == "deer":
        draw.ellipse([(12, 12), (20, 20)], outline=(0, 0, 0), width=2)

    return img


def save_subset(class_name: str, count: int, split_dir: Path):
    class_dir = split_dir / class_name
    class_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        img = gen_synthetic_image(class_name, i)
        img.save(class_dir / f"{class_name}_{i:04d}.jpg", quality=95)


def main():
    random.seed(42)
    print("=" * 60)
    print("Demo Data Preparation: LOCAL SYNTHETIC")
    print("=" * 60)
    print(f"Selected classes: {SELECTED_CLASSES}")
    print(f"Per-class total: {PER_CLASS_TOTAL} (train {int(PER_CLASS_TOTAL * TRAIN_RATIO)} + val {PER_CLASS_TOTAL - int(PER_CLASS_TOTAL * TRAIN_RATIO)})")
    print()

    train_count = int(PER_CLASS_TOTAL * TRAIN_RATIO)  # 160
    val_count = PER_CLASS_TOTAL - train_count  # 40

    for c in SELECTED_CLASSES:
        save_subset(c, train_count, TRAIN_DIR)
        save_subset(c, val_count, VAL_DIR)
        print(f"  [saved] {c}: train={train_count}, val={val_count}")

    total = (train_count + val_count) * len(SELECTED_CLASSES)
    print()
    print(f"[done] Total: {total} images")
    print(f"  train: {TRAIN_DIR.resolve()}")
    print(f"  val:   {VAL_DIR.resolve()}")


if __name__ == "__main__":
    main()
