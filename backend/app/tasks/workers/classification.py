"""
Celery Tasks: Model Training & Auto Annotate (v3.0.0 Phase 5 薄化)
===========================================
真正的训练逻辑 / 异步预标注, 在 Celery worker 中执行

**v3.0.0 Phase 5 重构**:
- 业务编排 (TrainingJob 状态机 / sticky_meta / 历史推送 / 失败清理) 全部下沉到
  TrainingLifecycleService, worker 主体从 ~585 行减到 ~300 行
- 兼容垫片 `_update_training_history` / `_persist_dataset_stats` 移至 TrainingLifecycleService
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.tasks.workers.celery_app import celery_app
from app.utils.async_helpers import run_async_in_worker as _run_async
from app.database.redis import redis_client

# ---- 在最早期禁用 HF symlink + 设置缓存目录 ----
_model_dir_env = os.getenv("MODEL_DIR", "./models")
_cache_dir_env = os.getenv("PRETRAINED_CACHE_DIR", str(Path(_model_dir_env) / "cache"))
os.environ.setdefault("HF_HOME", str(Path(_cache_dir_env) / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(Path(_cache_dir_env) / "torch"))
os.environ.setdefault("ULTRALYTICS_HOME", str(Path(_cache_dir_env) / "ultralytics"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
try:
    import huggingface_hub.constants as _hf_const
    _hf_const.HF_HUB_DISABLE_SYMLINKS = True
    _hf_const.HF_HUB_DISABLE_SYMLINKS_WARNING = True
    from app.core.config import settings as _settings
    if _settings.HF_ENDPOINT:
        _hf_const.HF_ENDPOINT = _settings.HF_ENDPOINT
        _hf_const.HUGGINGFACE_HUB_ENDPOINT = _settings.HUGGINGFACE_HUB_ENDPOINT
        os.environ.setdefault("HF_ENDPOINT", _settings.HF_ENDPOINT)
        os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", _settings.HUGGINGFACE_HUB_ENDPOINT)
except Exception:
    pass

from app.core.config import settings  # noqa: E402

# ---- v2.5.29: ultralytics 路径强制覆盖 ----
from app.tasks.ml.ultralytics_setup import configure_ultralytics, migrate_legacy_yolo_weights  # noqa: E402
configure_ultralytics()
migrate_legacy_yolo_weights()


@celery_app.task(bind=True)
def train_model_task(self, dataset_id: int, base_model: str, model_name: str,
                     user_id: int, epochs: int = 20, batch_size: int = 32,
                     learning_rate: float = 1e-4,
                     pretrained_model_path: Optional[str] = None):
    """
    异步训练任务 (Phase 5: 委托 TrainingLifecycleService)
    - 加载已确认/修正的图片 + 标签
    - 划分 train/val
    - 训练 N 个 epoch
    - 实时更新 progress
    - 保存最佳模型 + 评估指标
    - 写入 TrainingJob 任务历史
    """
    from app.tasks.ml.classification import run_training, TrainingPaused
    from app.tasks.service.training_data_service import TrainingDataService
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    task_id = self.request.id
    started_at = datetime.utcnow()
    total_epochs = epochs

    # ---- 1) 创建/复用 TrainingJob (委托 Service) ----
    job_id = TrainingLifecycleService.create_or_reset_job_sync(
        task_id=task_id,
        user_id=user_id,
        dataset_id=dataset_id,
        base_model=base_model,
        model_name=model_name,
        task_type="classification",
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        started_at=started_at,
    )

    history_buffer: list = []
    sticky_meta: dict = {}
    TrainingLifecycleService.set_last_sticky_meta(task_id, sticky_meta)

    def progress_cb(p: float, msg: str, extra: dict = None):
        """每 batch 进度回调"""
        meta = {
            "progress": round(p, 2),
            "msg": msg,
            "total_epochs": total_epochs,
        }
        if sticky_meta:
            meta.update(sticky_meta)
        if extra:
            meta.update(extra)
            sticky_meta.update(extra)
        TrainingLifecycleService.set_task_state(self, "PROGRESS", meta)
        # 持久化数据集统计到 DB
        if extra and "data_total" in extra and "num_classes" in extra:
            TrainingLifecycleService.persist_dataset_stats_sync(task_id, extra)

    def epoch_cb(p: float, msg: str, epoch_data: dict):
        """每 epoch 结束回调"""
        history_buffer.append(epoch_data)
        meta = {
            "progress": round(p, 2),
            "msg": msg,
            "total_epochs": total_epochs,
            "epoch": epoch_data.get("epoch"),
            "current_epoch": epoch_data.get("epoch"),
            "val_acc": epoch_data.get("val_acc"),
            "train_loss": epoch_data.get("train_loss"),
            "val_loss": epoch_data.get("val_loss"),
        }
        if sticky_meta:
            meta.update(sticky_meta)
        TrainingLifecycleService.set_task_state(self, "PROGRESS", meta)
        # 推历史曲线
        TrainingLifecycleService.push_history(
            task_id, list(history_buffer),
            job_id=job_id,
            progress=p,
            message=msg,
            current_epoch=epoch_data.get("epoch"),
        )

    def pause_check() -> bool:
        """每个 epoch 起点检查 Redis 暂停标志"""
        try:
            return redis_client.get(f"train:pause:{task_id}") is not None
        except Exception:
            return False

    # 启动时清掉旧暂停标志
    try:
        redis_client.delete(f"train:pause:{task_id}")
    except Exception:
        pass

    try:
        # v3.0.0 Phase 5: 注入 TrainingDataService 解耦 ML ↔ DB
        result = run_training(
            dataset_id=dataset_id,
            base_model=base_model,
            model_name=model_name,
            epochs=epochs,
            batch_size=batch_size,
            lr=learning_rate,
            progress_callback=progress_cb,
            epoch_callback=epoch_cb,
            pause_check=pause_check,
            pretrained_model_path=pretrained_model_path,
            data_loader=TrainingDataService.load_classification_samples_sync,
            model_saver=TrainingDataService.save_classification_model_version_sync,
        )

        # ---- 2) TrainingJob SUCCESS (委托 Service) ----
        async def _link_mv():
            """关联 ModelVersion (取 file_path 最新的)"""
            from sqlalchemy import select
            from app.database import AsyncSessionLocal
            from app.tasks.model.model_version import ModelVersion
            if not result.get("model_path"):
                return None
            async with AsyncSessionLocal() as db:
                stmt = (
                    select(ModelVersion)
                    .where(ModelVersion.file_path == result["model_path"])
                    .order_by(ModelVersion.id.desc())
                )
                return (await db.execute(stmt)).scalars().first()

        # ---- 3) 写 ModelVersion + 关联 ----
        dv_type = result.get("device_type")
        dv_info = result.get("device_info") or {}
        extra_fields: dict = {}
        if dv_type:
            extra_fields["device_type"] = str(dv_type)[:16]
        if isinstance(dv_info, dict) and dv_info:
            extra_fields["device_info"] = dv_info
            if dv_info.get("device_name"):
                extra_fields["device_name"] = str(dv_info["device_name"])[:128]
            peak = dv_info.get("gpu_peak_mb")
            if isinstance(peak, (int, float)):
                extra_fields["gpu_peak_memory_mb"] = int(peak)

        mv_id = None
        if result.get("model_path"):
            mv_id = TrainingLifecycleService.create_model_version_sync(
                name=model_name,
                base_model=base_model,
                dataset_id=dataset_id,
                task_type="classification",
                num_classes=0,  # classification 走 device_info 等额外字段
                file_path=result["model_path"],
                metrics=result.get("metrics", {}),
                history=history_buffer,
                extra_fields=extra_fields,
            )
            # 关联到 TrainingJob (若已存在同 file_path 的旧记录, 已通过 id desc 取最大, 这里新写)
            # 不需要再 link, Service 已经写好

        TrainingLifecycleService.mark_success_sync(
            job_id=job_id,
            started_at=started_at,
            history_buffer=history_buffer,
            message="Training completed",
            model_version_id=mv_id,
            sticky_meta=sticky_meta,
        )
        return {"status": "SUCCESS", "result": result, "job_id": job_id}

    except TrainingPaused as tp:
        # 用户主动暂停 (委托 Service, 包含清理半成品 ModelVersion + .pth)
        try:
            redis_client.delete(f"train:pause:{task_id}")
        except Exception:
            pass
        TrainingLifecycleService.mark_paused_sync(
            job_id=job_id,
            started_at=started_at,
            epoch=tp.epoch,
            total_epochs=tp.total_epochs,
            history_buffer=history_buffer,
            model_name=model_name,
        )
        # 走 update_state(REVOKED) 让 Celery 端状态正确
        TrainingLifecycleService.set_task_state(self, "REVOKED", {
            "progress": round(tp.epoch / max(tp.total_epochs, 1) * 100, 2),
            "msg": f"Paused at epoch {tp.epoch}/{tp.total_epochs}",
            "epoch": tp.epoch,
            "total_epochs": tp.total_epochs,
            "job_id": job_id,
        })
        return None

    except Exception as e:
        # 失败清理 (委托 Service, 含 sticky_meta 透传)
        TrainingLifecycleService.mark_failure_sync(
            job_id=job_id,
            error=str(e),
            started_at=started_at,
            exc_type=type(e).__name__,
            sticky_meta=sticky_meta,
        )
        # 错误详情单独存到 Redis 供前端读取
        try:
            redis_client.setex(
                f"train:error:{task_id}",
                86400,
                json.dumps({"error": str(e)[:500], "progress": 0.0, "status": "FAILURE"}),
            )
        except Exception:
            pass
        # 不返回 dict, 不 raise - Celery 看到 update_state FAILURE 后会标 task 失败
        return None


@celery_app.task(bind=True)
def auto_annotate_task(self, dataset_id: int, model_name: str,
                        confidence_threshold: float, user_id: int,
                        category_names: Optional[list] = None):
    """
    异步 AI 预标注任务 (大批量, Phase 5: 委托 AutoAnnotateService)
    - 接收 category_names 列表 (前端传入或从 DB 兜底加载)
    - base model 输出过滤到只含项目类目, 无匹配则不标注
    - 返回 { total, auto_labeled, need_human, no_match }
    """
    from app.tasks.service.auto_annotate_service import AutoAnnotateService
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    task_id = self.request.id
    TrainingLifecycleService.set_task_state(self, "PROGRESS", {
        "progress": 0, "msg": "Loading model..."
    })

    try:
        result = AutoAnnotateService.run(
            dataset_id=dataset_id,
            model_name=model_name,
            confidence_threshold=confidence_threshold,
            user_id=user_id,
            category_names=category_names,
            async_mode=False,  # worker 内部已经异步, 不需要再起任务
            progress_cb=lambda p, msg, **kw: TrainingLifecycleService.set_task_state(
                self, "PROGRESS", {"progress": p, "msg": msg, **kw}
            ),
        )
        return {"status": "SUCCESS", **result.to_dict()}
    except Exception as e:
        # meta 必须带 exc_type, 否则 Celery _store_result 抛 "Exception information
        # must include the exception type", 整个 worker 退出
        TrainingLifecycleService.set_task_state(self, "FAILURE", {
            "exc_type": type(e).__name__,
            "exc_message": str(e)[:200],
            "error": str(e)[:500],
        })
        return {"status": "FAILURE", "error": str(e)[:500]}


# ============== 兼容垫片 (Phase 5.6 清理) ==============
# 旧 detection/segmentation worker 通过 `from app.tasks.workers.classification import _update_training_history`
# 调用. Phase 5 重构后这些函数已迁移到 TrainingLifecycleService, 这里保留导入转发避免破坏.

def _update_training_history(task_id: str, history: list) -> None:
    """兼容垫片: 委托给 TrainingLifecycleService.push_history"""
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService
    TrainingLifecycleService.push_history(task_id, history)


def _persist_dataset_stats(task_id: str, extra: dict) -> None:
    """兼容垫片: 委托给 TrainingLifecycleService.persist_dataset_stats_sync"""
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService
    TrainingLifecycleService.persist_dataset_stats_sync(task_id, extra)
