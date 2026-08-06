"""
workers.segmentation.train 模块 — 图像分割训练任务
==================================================

**v3.0.0 Phase S6 拆分**: 从 workers/segmentation.py 抽离
**职责**: train_segmentation_task (DeepLabV3+ 异步训练) + _finish_failed 失败清理

**Celery 字符串路径**: `app.tasks.workers.segmentation:train_segmentation_task`
注意: Phase S6 拆分后, train_segmentation_task 物理位置在 train.py, 但 __init__.py 重新导出,
保持字符串路径和外部 import 完全向后兼容.

**训练流程** (Phase 5: 编排下沉到 TrainingLifecycleService):
1. TrainingLifecycleService.create_or_reset_job_sync — 创建/复用 TrainingJob
2. collect_segmentation_pairs — 加载 (image, mask) 配对
3. 计算 num_classes (类别数 + 背景 0)
4. 构造 sticky_meta (data_total/data_train/data_val/num_classes/class_names)
5. set_last_sticky_meta — 跨函数透传 sticky_meta (失败路径也能拿到)
6. train_segmentation — 纯 ML 训练 (DeepLabV3+)
7. 落盘 ModelVersion (.pt) + TrainingJob SUCCESS

**早期环境变量兜底** (与 detection/train.py 一致):
- HF_HOME / TORCH_HOME / ULTRALYTICS_HOME 相对路径
- HF_HUB_DISABLE_SYMLINKS / HF_HUB_DISABLE_SYMLINKS_WARNING
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.tasks.workers.celery_app import celery_app
from app.utils.async_helpers import run_async_in_worker as _run_async

# 早期: 与 detection/train.py 一致的 HF symlink + 缓存目录兜底
_model_dir_env = os.getenv("MODEL_DIR", "./models")
_cache_dir_env = os.getenv("PRETRAINED_CACHE_DIR", str(Path(_model_dir_env) / "cache"))
os.environ.setdefault("HF_HOME", str(Path(_cache_dir_env) / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(Path(_cache_dir_env) / "torch"))
os.environ.setdefault("ULTRALYTICS_HOME", str(Path(_cache_dir_env) / "ultralytics"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from app.core.config import settings  # noqa: E402

# v2.5.29: ultralytics 路径强制覆盖
from app.tasks.ml.ultralytics_setup import configure_ultralytics, migrate_legacy_yolo_weights  # noqa: E402
configure_ultralytics()
migrate_legacy_yolo_weights()


@celery_app.task(bind=True)
def train_segmentation_task(
    self,
    dataset_id: int,
    user_id: int,
    backbone: str = "deeplabv3_resnet50",
    model_alias: str = "deeplabv3_run",
    epochs: int = 3,
    batch_size: int = 4,
    crop_size: int = 256,
    learning_rate: float = 1e-4,
    device: str = "cpu",
    pretrained_model_path: Optional[str] = None,
    resume_from_epoch: int = 0,
):
    """启动分割训练 (DeepLabV3+, Phase 5: 编排下沉到 TrainingLifecycleService)

    v3.6.2: 新增 pretrained_model_path 参数, 断点续训用
    - None: 走 torchvision DeepLabV3 预训练骨干 + 随机初始化分类器头
    - 已存在路径: 加载 .pt 的 state_dict (strict=False 允许 num_classes 变化)

    v3.6.3: 新增 resume_from_epoch 参数 (断点续训用, 跳过前 N 个 epoch)
    - 0 (默认): 全新训练
    - >0:       断点续训, 从该 epoch (0-based) 开始
    - 配套: pretrained_model_path 需非空 (否则无 checkpoint 可用)
    """
    from app.tasks.ml.segmentation.seg_dataset import collect_segmentation_dataset_meta
    from app.tasks.ml.segmentation.seg_train import train_segmentation
    from app.tasks.ml.classification import TrainingPaused
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService
    # v3.5.0: 统一控制信号 (pause/cancel 区分)
    from app.tasks.workers.control_signals import (
        SignalAction,
        TaskCanceled,
        clear_all as clear_control_signals,
        make_pause_check,
    )

    task_id = self.request.id
    started_at = datetime.utcnow()

    # v3.5.0: 启动时清掉历史 pause/cancel 残留
    clear_control_signals(task_id)
    # v3.5.0: 工厂方法生成 pause_check 回调
    _pause_check_factory = make_pause_check(task_id)

    # ---- 1) 创建/复用 TrainingJob (委托 Service) ----
    job_id = TrainingLifecycleService.create_or_reset_job_sync(
        task_id=task_id,
        user_id=user_id,
        dataset_id=dataset_id,
        base_model=backbone,
        model_name=model_alias,
        task_type="segmentation",
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        started_at=started_at,
    )

    # ---- 2) 加载 (image, mask) pairs + 类目 (v3.5.0 Phase T7 #7: 一次 session 查) ----
    # 原实现: _load_pairs() (查 imgs + masks) + _load_categories() (单独 session 查 Category)
    # = 2 次 AsyncSession 创建 + 2 次 SQL round-trip (~5-30ms/次)
    # 修复: collect_segmentation_dataset_meta 共享同一 session, 共用 (dataset_id) 索引
    async def _load_pairs_and_categories():
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            return await collect_segmentation_dataset_meta(db, dataset_id)
    # v3.5.0 Phase T7 #7: 同时返回 imgs / masks / category_names
    # 原 _load_categories() 单独 session 已合并到这里 (共享同一 db session)
    images, masks, category_names = _run_async(_load_pairs_and_categories())
    if not images:
        _finish_failed(self, job_id, "数据集无 image+mask 配对, 请先上传 mask", started_at, task_id)
        return {"status": "FAILURE", "reason": "empty_dataset"}

    # v3.0.0: 采集训练设备信息 (CPU/GPU/CUDA/显存), 塞入 sticky_meta 供 mark_success 持久化
    # - 尽早采集, 失败路径也能通过 sticky_meta 记录运行设备
    _device_info_dict: dict = {}
    try:
        from app.tasks.ml.device_info import collect_device_info, extract_job_device_fields
        _device_info_dict = collect_device_info()
    except Exception:
        pass

    # ---- 3) 加载类目 (用于 sticky_meta) ----
    # v3.5.0 Phase T7 #7: 已在 _load_pairs_and_categories 一次性加载, 不再单独 session 查
    # 原 _load_categories() 闭包已删除 (消除冗余 AsyncSession + 1 次 SQL round-trip)
    n_total = len(masks)
    n_train = int(n_total * 0.8)
    n_val = n_total - n_train
    sticky_meta: dict = {
        "data_total": n_total,
        "data_train": n_train,
        "data_val": n_val,
        "num_classes": None,
        "class_names": category_names,  # v3.5.0 Phase T7 #7: 直接复用, 不再 sorted()
    }
    # v3.0.0: 合并设备信息字段 (供 mark_success/mark_failure 持久化到 TrainingJob)
    if _device_info_dict:
        try:
            from app.tasks.ml.device_info import extract_job_device_fields
            sticky_meta.update(extract_job_device_fields(_device_info_dict))
        except Exception:
            pass
    # 跨函数透传 (失败路径也能拿到)
    TrainingLifecycleService.set_last_sticky_meta(task_id, sticky_meta)

    history_buffer: list = []

    def _train_cb(stage, current, total, info, metrics=None):
        progress_pct = (current / total * 100.0) if total else 0.0
        epoch_msg = f"{stage} {current}/{total} {info}".strip()
        meta = {
            "stage": stage,
            "progress": round(progress_pct, 2),
            "current": current,
            "total": total,
            "current_epoch": current,
            "total_epochs": total,
            "msg": epoch_msg,
            "info": info,
        }
        if metrics and isinstance(metrics, dict):
            for _k in ("train_loss", "val_loss", "miou", "pixel_acc", "dice"):
                if _k in metrics and isinstance(metrics[_k], (int, float)):
                    meta[_k] = metrics[_k]
            history_buffer.append(metrics)
        meta.update(sticky_meta)
        # v3.5.0 Phase T7 #8: 合并 commit (log + progress + current_epoch + history)
        TrainingLifecycleService.set_task_state(
            self, "PROGRESS", meta,
            commit_progress=progress_pct,
            commit_message=epoch_msg,
            commit_current_epoch=current,
            commit_history=list(history_buffer) if history_buffer else None,
        )
        # 推历史曲线 (RPUSH 到 Redis, commit_db=False 避免重复写 DB)
        if history_buffer:
            TrainingLifecycleService.push_history(
                task_id, list(history_buffer),
                job_id=job_id,
                progress=progress_pct,
                message=epoch_msg,
                current_epoch=current,
                commit_db=False,  # Phase T7 #8: 已合并到 set_task_state
            )

    # ---- 4) 训练 ----
    # v3.6.4 HOTFIX: resume 模式加载已保存的历史曲线 (与 classification 对齐)
    # - 场景: 暂停 → 继续训练, 新 task 启动后 history_buffer = [] 会导致
    #         详情页曲线只显示 resume 后的数据
    # - 修复: worker 启动时, 显式从 DB 读出旧 history 预填到 history_buffer
    prior_history = TrainingLifecycleService.get_job_history_sync(job_id)
    if prior_history:
        history_buffer = list(prior_history)
        import logging as _seg_resume_log
        _seg_resume_log.getLogger(__name__).info(
            f"v3.6.4: segmentation resume 加载历史曲线, "
            f"{len(prior_history)} 个 epoch (从 epoch {resume_from_epoch} 续训)"
        )

    try:
        # v3.1.0 Phase W3.1: 复用已加载的 category_names 计算 num_classes, 消除重复 DB 查询
        # 旧实现用 _count_classes() 又查了一次 Category 表 (与 L106-114 完全相同)
        n_cat = len(category_names)
        num_classes = max(2, n_cat + 1)
        sticky_meta["num_classes"] = num_classes
        TrainingLifecycleService.set_last_sticky_meta(task_id, sticky_meta)

        # 数据集就绪推送 + 写库
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": 0.0,
            "msg": f"数据集就绪: train={n_train} val={n_val} num_classes={num_classes}",
            "total_epochs": epochs,
            **sticky_meta,
        })
        TrainingLifecycleService.persist_dataset_stats_sync(task_id, sticky_meta, job_id=job_id)

        result = train_segmentation(
            images=images, masks=masks,
            backbone=backbone, num_classes=num_classes,
            epochs=epochs, batch_size=batch_size,
            learning_rate=learning_rate, crop_size=crop_size,
            device=device, progress_cb=_train_cb,
            # v3.5.0: 注入 pause_check 回调, 让 train_segmentation 每个 epoch 起点
            # 检查暂停/取消信号 (SignalAction 枚举)
            pause_check=_pause_check_factory,
            # v3.6.2: 断点续训 — 加载 .pt 的 state_dict (strict=False 允许 num_classes 变化)
            pretrained_model_path=pretrained_model_path,
            # v3.6.3: 断点续训起始 epoch (0-based), 跳过前 N 个 epoch
            # 与 pretrained_model_path 配套使用: resume 时模型从 checkpoint 加载
            # 然后从 start_epoch 处继续训练
            start_epoch=resume_from_epoch,
        )
    except TaskCanceled as tc:
        # v3.5.0: 用户主动取消 (TaskCanceled 异常来自 train_segmentation 的 pause_check 回调)
        from app.database.redis import redis_client as _redis_for_cleanup
        try:
            _redis_for_cleanup.delete(f"train:cancel:{task_id}")
        except Exception:
            pass
        TrainingLifecycleService.mark_canceled_sync(
            job_id=job_id,
            started_at=started_at,
            epoch=tc.epoch,
            total_epochs=tc.total_epochs,
            history_buffer=history_buffer,
            model_name=model_alias,
            reason=getattr(tc, "reason", "user_cancel"),
        )
        # 走 update_state(REVOKED) 让 Celery 端状态正确
        # v3.5.0: 显式带 exc_type, 避免 Celery _store_result 抛 "Exception information
        # must include the exception type" 异常 (整个 worker 退出)
        TrainingLifecycleService.set_task_state(self, "REVOKED", {
            "progress": round(tc.epoch / max(tc.total_epochs, 1) * 100, 2),
            "msg": f"Canceled at epoch {tc.epoch}/{tc.total_epochs} ({getattr(tc, 'reason', 'user_cancel')})",
            "epoch": tc.epoch,
            "total_epochs": tc.total_epochs,
            "job_id": job_id,
            "exc_type": "TaskCanceled",
        })
        return None
    except TrainingPaused as tp:
        # v3.5.0: 用户主动暂停 (TrainingPaused 异常来自 train_segmentation 的 pause_check 回调)
        from app.database.redis import redis_client as _redis_for_cleanup
        try:
            _redis_for_cleanup.delete(f"train:pause:{task_id}")
        except Exception:
            pass
        TrainingLifecycleService.mark_paused_sync(
            job_id=job_id,
            started_at=started_at,
            epoch=tp.epoch,
            total_epochs=tp.total_epochs,
            history_buffer=history_buffer,
            model_name=model_alias,
        )
        # 走 update_state(REVOKED) 让 Celery 端状态正确
        # v3.5.0: 显式带 exc_type, 避免 Celery _store_result 抛 "Exception information
        # must include the exception type" 异常 (整个 worker 退出)
        TrainingLifecycleService.set_task_state(self, "REVOKED", {
            "progress": round(tp.epoch / max(tp.total_epochs, 1) * 100, 2),
            "msg": f"Paused at epoch {tp.epoch}/{tp.total_epochs}",
            "epoch": tp.epoch,
            "total_epochs": tp.total_epochs,
            "job_id": job_id,
            "exc_type": "TrainingPaused",
        })
        return None
    except Exception as e:
        _finish_failed(self, job_id, f"训练失败: {e}", started_at, task_id)
        return {"status": "FAILURE", "error": str(e)}

    # ---- 5) 落盘 ModelVersion + TrainingJob SUCCESS (委托 Service) ----
    # v3.3.0: 落点从 settings.MODEL_DIR/seg_runs 改到 settings.SEGMENTATION_MODEL_DIR,
    #         与 classification/detection 三个 task_type 平级, 都在 MODEL_DIR 下一级子目录.
    # v3.6.2: 文件名去掉 task_id 后缀 (旧版 `{model_alias}_{task_id}.pt`)
    #         原因: pause/resume 时 celery_task_id 会变, 按 model_alias 找旧 checkpoint 找不回来
    #         新版 `{model_alias}.pt` + seg_train.py 训练中也按此路径落盘,
    #         保证 resume 时同 model_alias 一定命中.
    weights_dir = settings.SEGMENTATION_MODEL_DIR
    weights_dir.mkdir(parents=True, exist_ok=True)
    weights_path = weights_dir / f"{model_alias}.pt"
    if result.get("state_dict_bytes"):
        weights_path.write_bytes(result["state_dict_bytes"])

    mv_id = TrainingLifecycleService.create_model_version_sync(
        name=model_alias,
        base_model=backbone,
        dataset_id=dataset_id,
        task_type="segmentation",
        num_classes=num_classes,
        file_path=str(weights_path.resolve()),
        metrics={
            "best_miou": result["best_miou"],
            "best_pix_acc": result["best_pix_acc"],
        },
        history=history_buffer,
    )
    TrainingLifecycleService.mark_success_sync(
        job_id=job_id,
        started_at=started_at,
        history_buffer=history_buffer,
        message=f"mIoU={result['best_miou']:.4f}",
        model_version_id=mv_id,
        sticky_meta=sticky_meta,
    )
    TrainingLifecycleService.set_task_state(self, "SUCCESS", {
        "model_version_id": mv_id, "best_miou": result["best_miou"],
    })
    return {
        "status": "SUCCESS", "model_version_id": mv_id,
        "best_miou": result["best_miou"],
    }


def _finish_failed(self, job_id: int, error_msg: str, started_at: datetime, task_id: str):
    """训练失败统一清理 (Phase 5: 委托 TrainingLifecycleService)

    注: 此函数在 segmentation/train.py 和 segmentation/auto_annotate.py 之间共享,
    __init__.py 重新导出.
    """
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    sticky_meta = TrainingLifecycleService.get_last_sticky_meta(task_id)
    TrainingLifecycleService.mark_failure_sync(
        job_id=job_id,
        error=error_msg,
        started_at=started_at,
        exc_type="SegmentationTrainError",
        sticky_meta=sticky_meta,
    )
    TrainingLifecycleService.set_task_state(self, "FAILURE", {
        "exc_type": "SegmentationTrainError",
        "exc_message": error_msg[:200],
        "error": error_msg[:500],
        "job_id": job_id,
    })
