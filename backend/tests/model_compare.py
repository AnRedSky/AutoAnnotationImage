"""
Model Comparison Experiment
===========================
在 CIFAR-10 5 类子集上跑 ResNet50 / EfficientNet-B0 / ConvNeXt-Tiny 各 5 epoch
输出 model_comparison.csv + 自动生成 docs/模型对比实验.md

用法:
    1) 准备数据:  python scripts/prepare_demo_data.py
    2) 把数据导入到系统中 (创建 dataset + 批量上传)
    3) 人工标注一批 (或调用 AI 预标注 + 强制采用)
    4) 跑对比:    python tests/model_compare.py
"""
import asyncio
import csv
import subprocess
import time
from pathlib import Path

import timm
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torchvision import transforms
from PIL import Image


MODELS = [
    ("resnet50", 25.6e6),
    ("efficientnet_b0", 5.3e6),
    ("convnext_tiny", 28.6e6),
]
DATA_DIR = Path("demo/data/cifar10_subset/train")
EPOCHS = 5
BATCH_SIZE = 16
LR = 1e-4
DEVICE = torch.device("cpu")
CLASSES = ["airplane", "automobile", "bird", "cat", "deer"]


class FolderDataset(torch.utils.data.Dataset):
    def __init__(self, root: Path, transform):
        self.samples = []
        self.transform = transform
        for cls_name in CLASSES:
            cls_dir = root / cls_name
            if not cls_dir.exists():
                continue
            for p in sorted(cls_dir.glob("*.jpg")):
                self.samples.append((p, CLASSES.index(cls_name)))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        p, label = self.samples[idx]
        img = Image.open(p).convert("RGB")
        return self.transform(img), label


def evaluate(model, loader):
    from sklearn.metrics import precision_score, recall_score, f1_score
    model.eval()
    preds, labels = [], []
    with torch.no_grad():
        for imgs, lbls in loader:
            imgs = imgs.to(DEVICE)
            out = model(imgs)
            preds.extend(out.argmax(1).cpu().tolist())
            labels.extend(lbls.tolist())
    acc = sum(p == l for p, l in zip(preds, labels)) / max(len(labels), 1)
    prec = precision_score(labels, preds, average="macro", zero_division=0)
    rec = recall_score(labels, preds, average="macro", zero_division=0)
    f1 = f1_score(labels, preds, average="macro", zero_division=0)
    return acc, prec, rec, f1


def run_one(model_name: str, params: int, train_loader, val_loader) -> dict:
    print(f"\n[run] {model_name} (params={params/1e6:.1f}M)")
    model = timm.create_model(model_name, pretrained=True, num_classes=len(CLASSES)).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    criterion = nn.CrossEntropyLoss()

    t0 = time.perf_counter()
    for epoch in range(EPOCHS):
        model.train()
        for imgs, lbls in train_loader:
            imgs, lbls = imgs.to(DEVICE), lbls.to(DEVICE)
            optimizer.zero_grad()
            loss = criterion(model(imgs), lbls)
            loss.backward()
            optimizer.step()
    train_time = time.perf_counter() - t0

    acc, prec, rec, f1 = evaluate(model, val_loader)
    print(f"  -> {train_time:.1f}s | acc={acc:.4f}  prec={prec:.4f}  rec={rec:.4f}  f1={f1:.4f}")
    return {
        "model": model_name,
        "params": int(params),
        "train_time_s": round(train_time, 1),
        "accuracy": round(acc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
    }


def write_csv(rows: list, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[saved] {path}")


def write_markdown(rows: list, path: Path):
    best = max(rows, key=lambda r: r["f1"])
    lines = [
        "# 模型对比实验报告",
        "",
        "> **编制日期**：2026-07-11",
        "> **数据集**：CIFAR-10 子集（5 类 × 200 张 = 1000 张，8:2 划分）",
        "> **超参数**：epochs=5, batch_size=16, lr=1e-4, optimizer=AdamW",
        f"> **结论**：{best['model']} 综合表现最佳（F1={best['f1']:.4f}）",
        "",
        "## 一、对比结果",
        "",
        "| 模型 | 参数量(M) | 训练时长(s) | Accuracy | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['params']/1e6:.1f} | {r['train_time_s']:.1f} "
            f"| {r['accuracy']:.4f} | {r['precision']:.4f} | {r['recall']:.4f} | {r['f1']:.4f} |"
        )

    lines += [
        "",
        "## 二、分析",
        "",
        f"1. **训练效率**：从训练时长看，{min(rows, key=lambda r: r['train_time_s'])['model']} "
        f"最快（{min(r['train_time_s'] for r in rows):.1f}s），适合 CPU 环境。",
        f"2. **精度表现**：{best['model']} 的 F1 分数最高（{best['f1']:.4f}），适合作为本系统默认模型。",
        f"3. **参数效率**：EfficientNet-B0 参数量最少（5.3M），但 F1 仍能达到 {next(r['f1'] for r in rows if r['model']=='efficientnet_b0'):.4f}，"
        f"在精度和推理速度之间取得平衡。",
        "",
        "## 三、本系统默认模型",
        "",
        f"基于上述对比，本系统选择 **{best['model']}** 作为默认预训练模型，"
        f"在 W2 / W3 阶段的所有 AI 预标注与训练任务中均使用此模型。",
        "",
        "## 四、复现",
        "",
        "```bash",
        "python scripts/prepare_demo_data.py     # 准备数据",
        "python tests/model_compare.py            # 跑对比 (CPU, 约 30 分钟)",
        "```",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[saved] {path}")


def main():
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    train_ds = FolderDataset(DATA_DIR, transform)
    val_ds = FolderDataset(DATA_DIR.parent / "val", transform)
    print(f"[data] train={len(train_ds)}, val={len(val_ds)}")
    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False)

    rows = [run_one(name, params, train_loader, val_loader) for name, params in MODELS]
    write_csv(rows, Path("tests/results/model_comparison.csv"))
    write_markdown(rows, Path("docs/模型对比实验.md"))


if __name__ == "__main__":
    main()
