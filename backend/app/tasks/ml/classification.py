"""
Classification Training Pipeline (v3.0.0 Stage 2.6 重定位)
============================================================
基于已确认标注, 训练 Fine-tune 模型.

**v3.0.0 Stage 2.6 迁移**: 原 app.tasks.ml.classification 重定位至 app.tasks.ml.classification,
app.tasks.ml.classification 转为兼容垫片 (re-export).
"""
import os
import platform
from pathlib import Path
from typing import Callable, Optional, Dict, Any
import timm
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from PIL import Image

from app.core.config import settings
# v3.0.0 Phase 5: ML 模块不再直接 import app.core.celery_utils (DB IO 已委托给 service)
# 当前 ML 模块已彻底解耦, 不再需要任何兼容垫片引用

# v3.0.0: 设备信息采集已抽离到共享模块, 供 classification/detection/segmentation 复用
from app.tasks.ml.device_info import collect_device_info, select_device  # noqa: F401

# v3.0.0: 不合格虚拟类别常量 + 软门禁阈值 (与 app.common.enums.UNQUALIFIED_LABEL 同义)
UNQUALIFIED_LABEL = "__unqualified__"
# 召回率阈值: 低于此值说明模型漏检不合格样本严重, 警告用户
UNQUALIFIED_RECALL_THRESHOLD = 0.5


class ImageClassificationDataset(Dataset):
    """已标注图片数据集"""

    def __init__(self, samples, transform=None):
        # samples: List of (image_path, label_idx)
        self.samples = samples
        self.transform = transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        return img, label


class TrainingPaused(Exception):
    """训练被用户暂停 — 由 pause_check 回调抛出, tasks.py 捕获后写 PAUSED 状态"""
    def __init__(self, epoch: int, total_epochs: int):
        self.epoch = epoch
        self.total_epochs = total_epochs
        super().__init__(f"Paused at epoch {epoch}/{total_epochs}")


def run_training(
    dataset_id: int,
    base_model: str = "efficientnet_b0",
    model_name: str = "v1",
    epochs: int = 20,
    batch_size: int = 32,
    lr: float = 1e-4,
    progress_callback: Optional[Callable] = None,
    epoch_callback: Optional[Callable] = None,
    early_stop_patience: int = 5,
    warmup_epochs: int = 1,
    pause_check: Optional[Callable[[], bool]] = None,
    pretrained_model_path: Optional[str] = None,
    data_loader: Optional[Callable[[int], Dict]] = None,
    model_saver: Optional[Callable[..., int]] = None,
) -> Dict:
    """
    完整训练流程 (同步, 在 Celery worker 中执行)

    参数:
        progress_callback(p, msg, extra): 每 batch 回调 (p: 0-100 进度)
            extra: dict, 可携带数据集统计 (total/train/val/num_classes) 等
        epoch_callback(p, msg, epoch_data): 每 epoch 回调
            epoch_data = {
                "epoch": int, "train_loss": float, "val_loss": float,
                "train_acc": float, "val_acc": float
            }
        pause_check() -> bool: 每个 epoch 起点回调. 返回 True 表示用户请求暂停,
            run_training 立即抛 TrainingPaused, 由调用方 (Celery task) 写 PAUSED 状态.
            检查粒度为 epoch 级, 不在 batch 中间停 (避免打断 dataloader 迭代器).
        pretrained_model_path: 增量训练 (再训练) 模式时, 传入已有 .pth 路径
            - None (默认): 从 timm ImageNet 预训练权重开始 (从头微调)
            - 已有路径: 加载该 .pth 的 state_dict 作为模型起点 (增量训练, fine-tune 旧模型)
            - 详见 _load_model_state 方法
        data_loader: 可选, 训练数据加载回调 (v3.0.0 Phase 5 新增)
            - 签名: (dataset_id: int) -> Dict[samples, label_name_to_idx, num_classes, ...]
            - 默认: 内部 DB IO (向后兼容, 走 ORM 模型层)
            - 推荐: 注入 TrainingDataService.load_classification_samples_sync
        model_saver: 可选, ModelVersion 保存回调 (v3.0.0 Phase 5 新增)
            - 签名: (**kwargs) -> int (ModelVersion.id)
            - 默认: 内部 DB IO (向后兼容)
            - 推荐: 注入 TrainingDataService.save_classification_model_version_sync
    """
    # v3.0.0 Phase 5: 注入数据加载 / 模型保存回调 (ML 解耦)
    # 若调用方未提供, 用默认实现 (内部走 app.tasks.model/app.database, 向后兼容)
    if data_loader is None:
        data_loader = _default_classification_data_loader
    if model_saver is None:
        model_saver = _default_classification_model_saver

    # ---- 核心: 自动选择最佳训练设备 (优先 GPU, 其次 MPS, 兜底 CPU) ----
    # 1) 采集设备硬件信息 (device_name / cuda / 显存 / CPU 核数 / RAM)
    # 2) 根据采集结果构造 torch.device
    # 3) 首次 progress_callback 推送设备信息, 让前端 SSE 实时显示
    # 4) 返回值里带 device_info, 由 tasks.py 写库 (TrainingJob.device_type + device_info)
    device_info = collect_device_info()
    device = select_device(device_info)
    if progress_callback:
        try:
            progress_callback(
                0.0,
                f"设备就绪: {device_info.get('device_name', '?')} ({device_info.get('device_type', 'cpu').upper()})",
                extra={"device_type": device_info.get("device_type"), "device_info": device_info},
            )
        except Exception:
            pass

    # v3.0.0 Phase 5: 数据加载委托给注入的 data_loader ----
    # 默认实现 (_default_classification_data_loader) 内部走 app.tasks.model
    # 推荐由 Worker 注入 TrainingDataService.load_classification_samples_sync
    data = data_loader(dataset_id)
    samples = data["samples"]
    label_name_to_idx = data["label_name_to_idx"]
    num_classes = data["num_classes"]
    skipped_orphan = data.get("skipped_orphan", 0)
    skipped_missing = data.get("skipped_missing", 0)
    n = data.get("total_before_filter", len(samples) + skipped_orphan + skipped_missing)

    # v3.0.0: 不合格虚拟类别纳入训练的关键数据 (供 model_saver 持久化 + 推理重建索引)
    # - class_names: 按 label_idx 顺序的类别名列表 (含 __unqualified__ 时末位即不合格类别)
    # - unqualified_count / unqualified_skipped: 降级标志, 由 worker 写入 TrainingJob 元数据
    class_names = data.get("class_names") or list(label_name_to_idx.keys())
    unqualified_count = int(data.get("unqualified_count", 0))
    unqualified_skipped = bool(data.get("unqualified_skipped", False))
    # 若回填/过滤后样本不够, 重新检查
    if len(samples) < 2:
        raise ValueError(
            f"已标注图片不足: 实际可用 {len(samples)} 张 (需 ≥2). "
            f"原已标注 {n} 张, 跳过 {skipped_orphan} 张孤儿 (ai_labeled 但 ai_prediction.top1 "
            f"不在项目类目) + {skipped_missing} 张磁盘文件缺失. "
            f"建议: 1) 在标注工作台手工确认几张图 (将状态从 ai_labeled 升到 human_confirmed), "
            f"2) 或补充更多图片再跑训练."
        )

    num_classes = len(label_name_to_idx)

    # 划分 train / val (80/20)
    split = int(len(samples) * 0.8)
    train_samples = samples[:split]
    val_samples = samples[split:] if split < len(samples) else samples[:5]

    # 数据集统计: 通过 progress_callback 的 extra 推送给前端
    # 前端 Training.vue 详情页会用这个展示「训练集/验证集/总样本/类数」四联统计
    if progress_callback:
        try:
            progress_callback(
                0.0,
                (
                    f"数据集已加载: 总样本 {len(samples)} 张 "
                    f"(训练集 {len(train_samples)}, 验证集 {len(val_samples)}), "
                    f"类别数 {num_classes}"
                ),
                extra={
                    "data_total": len(samples),
                    "data_train": len(train_samples),
                    "data_val": len(val_samples),
                    "num_classes": num_classes,
                    "class_names": list(label_name_to_idx.keys()),
                },
            )
        except Exception:
            pass

    # 数据增强
    train_transform = transforms.Compose([
        transforms.Resize(256),
        transforms.RandomCrop(224),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(0.2, 0.2, 0.2),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    val_transform = transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    train_loader = DataLoader(
        ImageClassificationDataset(train_samples, train_transform),
        batch_size=batch_size, shuffle=True, num_workers=0,
    )
    val_loader = DataLoader(
        ImageClassificationDataset(val_samples, val_transform),
        batch_size=batch_size, shuffle=False, num_workers=0,
    )

    # 构建模型
    # - 增量训练 (pretrained_model_path 非空): 先创建 timm 模型骨架 (num_classes=新类数),
    #   再加载 .pth 的 state_dict (strict=False 允许 final layer 形状不匹配)
    # - 从头训练 (默认): timm.create_model 加载 ImageNet 预训练权重 + num_classes=新类数
    model = timm.create_model(base_model, pretrained=(not pretrained_model_path), num_classes=num_classes)
    if pretrained_model_path:
        pth = Path(pretrained_model_path)
        if pth.exists():
            try:
                state_dict = torch.load(str(pth), map_location=device, weights_only=True)
                missing, unexpected = model.load_state_dict(state_dict, strict=False)
                # 增量训练场景下, num_classes 可能变化, 最后一层 (classifier) 会被自动 missing
                # (因为新模型的 num_classes 跟旧模型可能不同)
                if progress_callback:
                    progress_callback(
                        0.0,
                        f"已加载增量训练权重: {pth.name} (missing={len(missing)}, unexpected={len(unexpected)})",
                        extra={"pretrained_loaded": True, "pretrained_path": str(pth)},
                    )
            except Exception as e:
                # 加载失败, 兜底走随机初始化 (不让训练直接崩)
                if progress_callback:
                    progress_callback(
                        0.0,
                        f"⚠ 加载增量权重失败 ({type(e).__name__}: {e}), 改为随机初始化",
                        extra={"pretrained_loaded": False, "pretrained_error": str(e)[:200]},
                    )
        else:
            if progress_callback:
                progress_callback(
                    0.0,
                    f"⚠ 增量权重文件不存在: {pth}, 改为随机初始化",
                    extra={"pretrained_loaded": False, "pretrained_error": "file not found"},
                )
    model.to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    criterion = nn.CrossEntropyLoss()
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs - warmup_epochs, 1))

    # 训练循环
    history = {"train_loss": [], "val_loss": [], "train_acc": [], "val_acc": []}
    best_acc = 0.0
    best_state = None
    no_improve_count = 0  # 早停计数器

    for epoch in range(epochs):
        # ---- 暂停检查: 每个 epoch 起点 (避免打断 DataLoader 迭代器) ----
        if pause_check and pause_check():
            raise TrainingPaused(epoch=epoch + 1, total_epochs=epochs)

        # ---- Warmup: 线性增加 LR ----
        if epoch < warmup_epochs:
            warmup_lr = lr * (epoch + 1) / warmup_epochs
            for pg in optimizer.param_groups:
                pg["lr"] = warmup_lr

        # Train
        model.train()
        t_loss, t_correct, t_total = 0, 0, 0
        total_batches = max(len(train_loader), 1)
        for batch_idx, (imgs, labels) in enumerate(train_loader):
            imgs, labels = imgs.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(imgs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            t_loss += loss.item()
            _, pred = outputs.max(1)
            t_total += labels.size(0)
            t_correct += pred.eq(labels).sum().item()

            # 每 100 batch 回调 (替代原 5 batch)
            if progress_callback and batch_idx % 100 == 0:
                p = (epoch * total_batches + batch_idx) / (epochs * total_batches) * 100
                progress_callback(p, f"Epoch {epoch+1}/{epochs} batch {batch_idx}/{total_batches}")

        # Validation
        model.eval()
        v_loss, v_correct, v_total = 0, 0, 0
        all_preds, all_labels = [], []
        with torch.no_grad():
            for imgs, labels in val_loader:
                imgs, labels = imgs.to(device), labels.to(device)
                outputs = model(imgs)
                v_loss += criterion(outputs, labels).item()
                _, pred = outputs.max(1)
                v_total += labels.size(0)
                v_correct += pred.eq(labels).sum().item()
                all_preds.extend(pred.cpu().tolist())
                all_labels.extend(labels.cpu().tolist())

        # 只有过了 warmup 期才让 scheduler 接管
        if epoch >= warmup_epochs:
            scheduler.step()

        epoch_t_acc = t_correct / max(t_total, 1)
        epoch_v_acc = v_correct / max(v_total, 1)
        history["train_loss"].append(t_loss / total_batches)
        history["val_loss"].append(v_loss / max(len(val_loader), 1))
        history["train_acc"].append(epoch_t_acc)
        history["val_acc"].append(epoch_v_acc)

        if epoch_v_acc > best_acc:
            best_acc = epoch_v_acc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            no_improve_count = 0
        else:
            no_improve_count += 1

        if progress_callback:
            progress_callback(
                (epoch + 1) / epochs * 100,
                f"Epoch {epoch+1}/{epochs} done | val_acc={epoch_v_acc:.4f}"
            )

        # 每 epoch 回调 (供 Celery 持久化训练曲线)
        if epoch_callback:
            epoch_data = {
                "epoch": epoch + 1,
                "train_loss": history["train_loss"][-1],
                "val_loss": history["val_loss"][-1],
                "train_acc": history["train_acc"][-1],
                "val_acc": history["val_acc"][-1],
            }
            # GPU 显存峰值监控: 仅在 CUDA 设备上有意义
            if device_info.get("device_type") == "cuda":
                try:
                    peak_mb = round(torch.cuda.max_memory_allocated(0) / (1024 ** 2))
                    curr_mb = round(torch.cuda.memory_allocated(0) / (1024 ** 2))
                    epoch_data["gpu_peak_mb"] = peak_mb
                    epoch_data["gpu_curr_mb"] = curr_mb
                except Exception:
                    pass
            epoch_callback(
                (epoch + 1) / epochs * 100,
                f"Epoch {epoch+1}/{epochs} done | val_acc={epoch_v_acc:.4f}",
                epoch_data,
            )

        # ---- 早停 ----
        if no_improve_count >= early_stop_patience:
            print(f"[train] Early stop at epoch {epoch+1} (no improve for {early_stop_patience} epochs)")
            break

    # 保存最佳模型
    model_path = settings.MODEL_DIR / f"{model_name}_best.pth"
    if best_state:
        torch.save(best_state, model_path)

    # 计算混淆矩阵
    from sklearn.metrics import confusion_matrix, classification_report
    cm = confusion_matrix(all_labels, all_preds) if all_labels else []
    report = classification_report(all_labels, all_preds, output_dict=True, zero_division=0)

    # 关键: 转换 numpy → python 原生类型, 否则 Celery 序列化 result 时
    # 会报 "Object of type ndarray is not JSON serializable", 把已成功
    # 的 task 标记为 FAILURE (前端看到红但 DB 实际 SUCCESS, 历史上最
    # 容易让用户误判的 bug)
    cm_json = cm.tolist() if hasattr(cm, "tolist") else (cm if isinstance(cm, list) else [])

    # v3.0.0: 不合格类别准确率软门禁
    # 训练纳入 __unqualified__ 虚拟类别时, 从 classification_report 提取该类别的
    # precision / recall / f1, 低于阈值则警告 (不阻断训练, 仅提示用户模型可能不可靠)
    # - 阈值: UNQUALIFIED_METRIC_THRESHOLD (recall 低于此值, 模型容易漏检不合格)
    # - 警告通过 sticky_meta 透传给前端 SSE (worker 中读取 result["unqualified_warning"])
    unqualified_warning: Optional[str] = None
    if UNQUALIFIED_LABEL in class_names:
        uq_metrics = report.get(UNQUALIFIED_LABEL, {}) if isinstance(report, dict) else {}
        uq_recall = float(uq_metrics.get("recall", 0.0) or 0.0)
        uq_precision = float(uq_metrics.get("precision", 0.0) or 0.0)
        uq_f1 = float(uq_metrics.get("f1-score", 0.0) or 0.0)
        if uq_recall < UNQUALIFIED_RECALL_THRESHOLD:
            unqualified_warning = (
                f"不合格类别召回率偏低 (recall={uq_recall:.2f} < {UNQUALIFIED_RECALL_THRESHOLD}), "
                f"模型可能漏检不合格样本. precision={uq_precision:.2f}, f1={uq_f1:.2f}. "
                f"建议: 增加不合格样本数 (当前 {unqualified_count} 张) 后再训练."
            )

    # ---- 训练资源记录: 把设备信息 + GPU 峰值显存 (CUDA 时) 加到返回值 ----
    # tasks.py 会把 device_type + device_info 写到 TrainingJob 表 (DB 持久化)
    # 前端 Training.vue 通过 /api/training/jobs/{id} 拉到 device_info 后展示
    # 必须在 model_saver 调用前完成 final_device_info 组装, 否则 model_saver 写入 DB
    # 的 device_info 字段为空, 且会因为访问未定义变量 UnboundLocalError
    final_device_info = dict(device_info)  # 浅拷贝避免污染外层
    if device_info.get("device_type") == "cuda":
        try:
            peak_mb = round(torch.cuda.max_memory_allocated(0) / (1024 ** 2))
            final_device_info["gpu_peak_mb"] = peak_mb
        except Exception:
            pass
        # 重置 cuda 统计 (避免下次跑任务时复用旧 peak)
        try:
            torch.cuda.reset_peak_memory_stats(0)
        except Exception:
            pass

    # 持久化到数据库 (创建 ModelVersion 记录, is_active 默认为 False)
    # 激活操作由前端通过 POST /api/models/{id}/activate 触发, 保证互斥
    # v3.0.0 Phase 5: 委托给注入的 model_saver
    # v3.0.0: 传 class_names 让 ModelVersion 保存索引→类别名映射 (含 __unqualified__),
    # 推理时按此映射重建, 命中 __unqualified__ 索引 → 自动标记图片为不合格
    new_model_version_id = model_saver(
        name=model_name,
        base_model=base_model,
        dataset_id=dataset_id,
        num_classes=num_classes,
        file_path=str(model_path),
        accuracy=best_acc,
        report=report,
        history=history,
        confusion_matrix=cm,
        device_info=final_device_info,
        class_names=class_names,
    )

    return {
        "model_path": str(model_path),
        "best_accuracy": float(best_acc) if best_acc is not None else 0.0,
        "history": history,
        "confusion_matrix": cm_json,
        "model_version_id": int(new_model_version_id) if new_model_version_id is not None else None,
        "device_type": str(device_info.get("device_type", "cpu")),
        "device_info": final_device_info,
        # v3.0.0: 不合格虚拟类别纳入训练的元信息 (供 worker 写 TrainingJob.sticky_meta)
        "class_names": class_names,
        "unqualified_count": unqualified_count,
        "unqualified_skipped": unqualified_skipped,
        # v3.0.0: 软门禁警告 (None=未启用 / 已达标; 字符串=不达标, 前端 SSE 显示)
        "unqualified_warning": unqualified_warning,
    }


# ============== 默认 data_loader / model_saver 实现 (v3.0.0 Phase 5) ==============
# 当 run_training 调用方未注入回调时, 用这两个默认实现 (内部走 app.tasks.model/app.database).
# 推荐 Worker 注入 TrainingDataService 的实现 (解耦 ML ↔ DB).

def _default_classification_data_loader(dataset_id: int) -> Dict:
    """默认分类数据加载 (向后兼容, 内部走 app.tasks.model)

    v3.0.0 Phase 5 重构: 等价于 TrainingDataService.load_classification_samples,
    保留为默认 fallback, 让 ML 模块在未注入回调时仍可独立运行.
    """
    from app.tasks.service.training_data_service import TrainingDataService
    return TrainingDataService.load_classification_samples_sync(dataset_id)


def _default_classification_model_saver(**kwargs) -> int:
    """默认 ModelVersion 保存 (向后兼容, 内部走 app.tasks.model)"""
    from app.tasks.service.training_data_service import TrainingDataService
    return TrainingDataService.save_classification_model_version_sync(**kwargs)
