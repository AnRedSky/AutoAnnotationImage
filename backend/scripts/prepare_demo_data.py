"""
Demo Data Preparation Script
============================
下载 CIFAR-10, 抽取 5 类 × 200 张, 8:2 划分 train/val

用法:
    python scripts/prepare_demo_data.py

输出:
    demo/data/cifar10_subset/train/{class_name}/*.jpg  (5 × 160 = 800 张)
    demo/data/cifar10_subset/val/{class_name}/*.jpg    (5 × 40 = 200 张)
"""
import os
import sys
import random
import pickle
import urllib.request
import tarfile
from pathlib import Path
from PIL import Image


# 5 类 CIFAR-10 (论文中"垃圾分类 5 类"的演示, 用 CIFAR-10 子集代替)
SELECTED_CLASSES = ["airplane", "automobile", "bird", "cat", "deer"]
PER_CLASS_TOTAL = 200  # 每类 200 张
TRAIN_RATIO = 0.8
CIFAR10_URL = "https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz"
CIFAR10_DIR = Path("demo/data/cifar10_raw")
OUTPUT_DIR = Path("demo/data/cifar10_subset")
TRAIN_DIR = OUTPUT_DIR / "train"
VAL_DIR = OUTPUT_DIR / "val"


def download_cifar10():
    """下载 CIFAR-10 数据集 (~170MB)"""
    tar_path = CIFAR10_DIR / "cifar-10-python.tar.gz"
    if tar_path.exists():
        print(f"[skip] {tar_path} already exists")
        return tar_path
    CIFAR10_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[download] {CIFAR10_URL} -> {tar_path}")
    urllib.request.urlretrieve(CIFAR10_URL, tar_path)
    print(f"[ok] Downloaded {tar_path.stat().st_size / 1024 / 1024:.1f} MB")
    return tar_path


def extract_cifar10(tar_path: Path):
    """解压 CIFAR-10"""
    extract_dir = CIFAR10_DIR / "cifar-10-batches-py"
    if extract_dir.exists():
        print(f"[skip] {extract_dir} already extracted")
        return extract_dir
    print(f"[extract] {tar_path}")
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(CIFAR10_DIR)
    print(f"[ok] Extracted to {extract_dir}")
    return extract_dir


def load_cifar10_images(extract_dir: Path):
    """
    加载所有图片, 按类返回 {label_name: [PIL.Image]}
    CIFAR-10 格式: 5 个 data_batch_1..5, 每个含 10000 张 32x32x3
    """
    label_names = [
        "airplane", "automobile", "bird", "cat", "deer",
        "dog", "frog", "horse", "ship", "truck",
    ]
    selected_indices = [label_names.index(c) for c in SELECTED_CLASSES]
    label_map = {i: label_names[i] for i in selected_indices}

    images_by_label = {c: [] for c in SELECTED_CLASSES}
    for batch_id in range(1, 6):
        batch_file = extract_dir / f"data_batch_{batch_id}"
        with open(batch_file, "rb") as f:
            entry = pickle.load(f, encoding="bytes")
        data = entry[b"data"]  # (N, 3072) uint8
        labels = entry[b"labels"]
        print(f"  [load] batch {batch_id}: {data.shape[0]} images")
        for i in range(data.shape[0]):
            label = labels[i]
            if label in selected_indices:
                # CIFAR-10 图像格式: RRRR...GGGG...BBBB
                r = data[i, 0:1024].reshape(32, 32)
                g = data[i, 1024:2048].reshape(32, 32)
                b = data[i, 2048:3072].reshape(32, 32)
                img = Image.fromarray(
                    __import__("numpy").stack([r, g, b], axis=2)
                )
                images_by_label[label_map[label]].append(img)

    return images_by_label


def save_subset(images_by_label: dict, output_dir: Path, split: str, count_per_class: int):
    """保存子集到 {output_dir}/{split}/{class_name}/*.jpg"""
    split_dir = output_dir / split
    split_dir.mkdir(parents=True, exist_ok=True)
    for class_name, images in images_by_label.items():
        class_dir = split_dir / class_name
        class_dir.mkdir(parents=True, exist_ok=True)
        sample = random.sample(images, min(count_per_class, len(images)))
        for i, img in enumerate(sample):
            img.save(class_dir / f"{class_name}_{i:04d}.jpg", quality=95)
    print(f"  [saved] {split}: {count_per_class} images × {len(SELECTED_CLASSES)} classes = {count_per_class * len(SELECTED_CLASSES)} files")


def main():
    random.seed(42)
    print("=" * 60)
    print("Demo Data Preparation: CIFAR-10 Subset")
    print("=" * 60)
    print(f"Selected classes: {SELECTED_CLASSES}")
    print(f"Per-class total: {PER_CLASS_TOTAL} (train {int(PER_CLASS_TOTAL * TRAIN_RATIO)} + val {PER_CLASS_TOTAL - int(PER_CLASS_TOTAL * TRAIN_RATIO)})")
    print()

    tar_path = download_cifar10()
    extract_dir = extract_cifar10(tar_path)
    images_by_label = load_cifar10_images(extract_dir)

    print()
    print(f"[summary] Loaded {sum(len(v) for v in images_by_label.values())} images in {len(SELECTED_CLASSES)} classes")
    for c, imgs in images_by_label.items():
        print(f"  - {c}: {len(imgs)} available")

    # 划分 8:2
    train_count = int(PER_CLASS_TOTAL * TRAIN_RATIO)  # 160
    val_count = PER_CLASS_TOTAL - train_count  # 40

    print()
    print(f"[save] train: {train_count} per class, val: {val_count} per class")
    save_subset(images_by_label, OUTPUT_DIR, "train", train_count)
    save_subset(images_by_label, OUTPUT_DIR, "val", val_count)

    print()
    print(f"[done] Output: {OUTPUT_DIR.resolve()}")
    print(f"  train: {TRAIN_DIR.resolve()}")
    print(f"  val:   {VAL_DIR.resolve()}")


if __name__ == "__main__":
    main()
