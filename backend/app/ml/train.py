"""
Model Training Pipeline
========================
基于已确认标注, 训练 Fine-tune 模型
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

from app.config import settings
from app.core.celery_utils import run_async_in_worker as _run_async


def collect_device_info() -> Dict[str, Any]:
    """
    采集训练资源信息 (device_type / device_name / CUDA / GPU 显存 / CPU 核数 / RAM / torch 版本 / python 版本).
    - 优先 CUDA (Nvidia GPU)
    - 其次 MPS (Apple Silicon Mac Metal)
    - 兜底 CPU

    返回 dict 全部字段都是 JSON 安全的 (str / int / float / None), 序列化时不会爆炸.
    用于写入 TrainingJob.device_info (DB JSON 字段) + SSE meta 推给前端展示.
    """
    info: Dict[str, Any] = {
        "device_type": "cpu",
        "device_name": platform.processor() or "CPU",
        "device_index": 0,
        "cuda_version": None,
        "cudnn_version": None,
        "torch_version": torch.__version__,
        "python_version": platform.python_version(),
        "os_platform": platform.platform(),
        "gpu_count": 0,
        "gpu_memory_mb": None,
        "gpu_memory_total_mb": None,
        "cpu_count": os.cpu_count(),
        "ram_gb": None,
    }
    # RAM (可选: psutil 没装就跳过)
    try:
        import psutil
        vmem = psutil.virtual_memory()
        info["ram_gb"] = round(vmem.total / (1024 ** 3), 2)
        info["ram_available_gb"] = round(vmem.available / (1024 ** 3), 2)
    except Exception:
        pass
    # CUDA 优先
    if torch.cuda.is_available():
        try:
            info["device_type"] = "cuda"
            info["gpu_count"] = torch.cuda.device_count()
            info["device_name"] = torch.cuda.get_device_name(0)
            info["device_index"] = 0
            info["cuda_version"] = torch.version.cuda
            try:
                info["cudnn_version"] = torch.backends.cudnn.version()
            except Exception:
                pass
            props = torch.cuda.get_device_properties(0)
            info["gpu_memory_total_mb"] = round(props.total_memory / (1024 ** 2))
            # 当前空闲显存 (训练前的 snapshot, 真实峰值在 GPU 监控回调里更新)
            try:
                free, total = torch.cuda.mem_get_info(0)
                info["gpu_memory_mb"] = round(total / (1024 ** 2))
            except Exception:
                pass
        except Exception as e:
            info["device_name"] = f"CUDA available but device detection failed: {e!r}"
    # MPS (Apple Silicon)
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        try:
            info["device_type"] = "mps"
            info["device_name"] = f"Apple Silicon MPS ({platform.processor()})"
        except Exception:
            info["device_name"] = "Apple Silicon MPS"
    return info


def select_device(device_info: Dict[str, Any]) -> torch.device:
    """
    核心要求: 优先 GPU (CUDA), 其次 MPS, 兜底 CPU.
    - 若 torch.cuda.is_available() 报 True 但 load 失败, 退回 CPU 并通过
      device_info["fallback_reason"] 记录原因 (供前端显示「CUDA 不可用, 已退回 CPU」)
    """
    preferred = device_info.get("device_type", "cpu")
    try:
        if preferred == "cuda":
            return torch.device("cuda")
        if preferred == "mps":
            return torch.device("mps")
    except Exception as e:
        device_info["fallback_reason"] = f"{preferred} device construction failed: {e!r}"
    return torch.device("cpu")


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
    """
    from app.database import AsyncSessionLocal
    from app.models.image import Image
    from app.models.category import Category
    from app.models.model_version import ModelVersion
    from sqlalchemy import select

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

    async def _load_data():
        async with AsyncSessionLocal() as db:
            # 加载已确认标注的图片
            # 可训练状态:
            #   - human_confirmed / human_corrected: 人工确认/修正 (论文主流程)
            #   - ai_labeled: AI 自动标注 (演示/快速验证场景, 用户主动接受 AI 标签即可训练)
            #
            # 用 LEFT JOIN 兼容 final_label_id=NULL 的 "孤儿" ai_labeled 图 (老数据, 当年
            # auto-label 还没写 final_label_id 字段), 走 ai_prediction.top1 回查 Category
            # 并 in-place 回填 final_label_id. 这样训练不会因为一两条历史脏数据而失败.
            stmt = (
                select(Image, Category)
                .outerjoin(Category, Image.final_label_id == Category.id)
                .where(
                    Image.dataset_id == dataset_id,
                    Image.status.in_(["human_confirmed", "human_corrected", "ai_labeled"]),
                )
            )
            results = (await db.execute(stmt)).all()
            categories = {
                c.id: c.name
                for c in (await db.execute(
                    select(Category).where(Category.dataset_id == dataset_id)
                )).scalars().all()
            }

            # 收集需要回填 final_label_id 的 ai_labeled 孤儿图, 一次写回
            orphans_to_backfill: list[tuple[int, int]] = []  # (image_id, category_id)
            for img, cat in results:
                if cat is None and img.status == "ai_labeled" and img.ai_prediction:
                    top1 = (img.ai_prediction or {}).get("top1")
                    if top1 and top1 in categories.values():
                        cat_id = next(
                            cid for cid, cname in categories.items() if cname == top1
                        )
                        orphans_to_backfill.append((img.id, cat_id))
            if orphans_to_backfill:
                from app.models.image import Image as _Image
                for img_id, cat_id in orphans_to_backfill:
                    img_row = await db.get(_Image, img_id)
                    if img_row and img_row.final_label_id is None:
                        img_row.final_label_id = cat_id
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
        return results, categories

    image_label_pairs, categories = _run_async(_load_data())
    # 训练最低门槛: 至少 2 张已标注图片 (冷启动兜底 — 用户拿到第一个 fine-tune 模型即可
    # 再回过头来用此模型做预标注扩充数据). 显式提示差几张 + 哪几种状态被纳入统计,
    # 让用户知道下一步该做什么 (人工标注 / 跑 AI 预标注 / 确认 AI 标签).
    n = len(image_label_pairs)
    if n < 2:
        raise ValueError(
            f"已标注图片不足: 当前 {n} 张 (需 ≥2). "
            f"状态纳入: human_confirmed / human_corrected / ai_labeled. "
            f"建议: 1) 在标注工作台手工标注几张, 或 2) 启动 AI 预标注 (会基于 ImageNet 基础模型回退). "
            f"数据集中在数 AI 预标注后请到标注工作台点击「确认」将状态从 ai_labeled 升级到 human_confirmed."
        )

    # 构建 (path, label_idx) 列表
    # - 过滤掉 LEFT JOIN 后 cat is None 的孤儿图 (回填失败的 ai_labeled, ai_prediction.top1
    #   不在项目类目内, 或 ai_prediction 本身就为空)
    # - 过滤掉磁盘文件丢失的图
    label_name_to_idx = {name: i for i, name in enumerate(sorted(set(categories.values())))}
    samples = []
    skipped_orphan = 0
    skipped_missing = 0
    for img, cat in image_label_pairs:
        if cat is None:
            skipped_orphan += 1
            continue
        full_path = settings.UPLOAD_DIR / img.storage_path
        if not full_path.exists():
            skipped_missing += 1
            continue
        samples.append((str(full_path), label_name_to_idx[cat.name]))
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

    # 持久化到数据库 (创建 ModelVersion 记录, is_active 默认为 False)
    # 激活操作由前端通过 POST /api/models/{id}/activate 触发, 保证互斥
    async def _save_model_version():
        async with AsyncSessionLocal() as db:
            from app.models.model_version import ModelVersion
            mv = ModelVersion(
                name=model_name,
                base_model=base_model,
                dataset_id=dataset_id,
                num_classes=num_classes,
                file_path=str(model_path),
                accuracy=best_acc,
                precision=report.get("macro avg", {}).get("precision", 0),
                recall=report.get("macro avg", {}).get("recall", 0),
                f1_score=report.get("macro avg", {}).get("f1-score", 0),
                training_log=history,
                confusion_matrix=cm.tolist() if hasattr(cm, "tolist") else cm,
                is_active=False,  # 不再自动激活, 需通过 API 显式激活
            )
            db.add(mv)
            await db.commit()
            await db.refresh(mv)
            return mv.id

    new_model_version_id = _run_async(_save_model_version())

    # 关键: 转换 numpy → python 原生类型, 否则 Celery 序列化 result 时
    # 会报 "Object of type ndarray is not JSON serializable", 把已成功
    # 的 task 标记为 FAILURE (前端看到红但 DB 实际 SUCCESS, 历史上最
    # 容易让用户误判的 bug)
    cm_json = cm.tolist() if hasattr(cm, "tolist") else (cm if isinstance(cm, list) else [])

    # ---- 训练资源记录: 把设备信息 + GPU 峰值显存 (CUDA 时) 加到返回值 ----
    # tasks.py 会把 device_type + device_info 写到 TrainingJob 表 (DB 持久化)
    # 前端 Training.vue 通过 /api/training/jobs/{id} 拉到 device_info 后展示
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

    return {
        "model_path": str(model_path),
        "best_accuracy": float(best_acc) if best_acc is not None else 0.0,
        "history": history,
        "confusion_matrix": cm_json,
        "model_version_id": int(new_model_version_id) if new_model_version_id is not None else None,
        "device_type": str(device_info.get("device_type", "cpu")),
        "device_info": final_device_info,
    }
