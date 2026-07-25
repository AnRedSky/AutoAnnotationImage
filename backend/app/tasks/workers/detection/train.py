"""
workers.detection.train 模块 — YOLOv8 训练任务
==============================================

**v3.0.0 Phase S4 拆分**: 从 workers/detection.py 抽离
**职责**: train_detection_task (异步 YOLOv8 训练) + _finish_failed 失败清理

**Celery 字符串路径**: `app.tasks.workers.detection:train_detection_task`
注意: Phase S4 拆分后, train_detection_task 物理位置在 train.py, 但 __init__.py 重新导出,
保持字符串路径和外部 import 完全向后兼容.

**训练流程** (Phase 5: 编排下沉到 TrainingLifecycleService):
1. TrainingLifecycleService.create_or_reset_job_sync — 创建/复用 TrainingJob
2. 共享状态 sticky_meta + history_buffer + 临时数据集目录 workdir
3. _export_cb / _train_cb — 进度回调 → Celery state
4. export_yolo_dataset — 导 YOLO 数据集 (含 train/val 拆分 + data.yaml)
5. set_last_sticky_meta — 跨函数透传 sticky_meta (失败路径也能拿到)
6. persist_dataset_stats_sync — 数据集统计写库 + 推送
7. train_yolo — 纯 ML 训练 (ultralytics)
8. create_model_version_sync — 写 ModelVersion
9. mark_success_sync — TrainingJob SUCCESS + 推 history

**失败路径**:
- YoloTrainError / Exception → _finish_failed 统一清理
- finally: 清理临时 workdir (shutil.rmtree)
"""
import shutil
from datetime import datetime
from pathlib import Path

from app.tasks.workers.celery_app import celery_app
from app.utils.async_helpers import run_async_in_worker as _run_async
from app.core.config import settings


@celery_app.task(bind=True)
def train_detection_task(
    self,
    dataset_id: int,
    user_id: int,
    model_name: str = "yolov8n",
    model_alias: str = "yolov8n_run",
    epochs: int = 10,
    imgsz: int = 320,
    batch: int = 8,
    val_ratio: float = 0.2,
    device: str = "cpu",
):
    """异步 YOLOv8 训练 (Phase 5: 编排下沉到 TrainingLifecycleService)"""
    from app.tasks.ml.detection import export_yolo_dataset, train_yolo, YoloTrainError
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    task_id = self.request.id
    started_at = datetime.utcnow()

    # ---- 1) 创建/复用 TrainingJob (委托 Service) ----
    job_id = TrainingLifecycleService.create_or_reset_job_sync(
        task_id=task_id,
        user_id=user_id,
        dataset_id=dataset_id,
        base_model=model_name,
        model_name=model_alias,
        task_type="detection",
        epochs=epochs,
        batch_size=batch,
        learning_rate=0.0,
        started_at=started_at,
    )

    # ---- 2) 共享状态 ----
    sticky_meta: dict = {}
    history_buffer: list = []
    workdir = settings.DATA_DIR / "yolo" / f"{model_alias}_{task_id}"

    def _export_cb(stage, current, total, info=""):
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": round(current / max(total, 1) * 100, 2),
            "msg": f"[{stage}] {info}",
            "total_epochs": epochs,
            **sticky_meta,
        })

    def _train_cb(stage, current_epoch, total_epochs, metrics):
        history_buffer.append({
            "epoch": current_epoch,
            "total_epochs": total_epochs,
            **metrics,
        })
        progress_pct = round(current_epoch / max(total_epochs, 1) * 100, 2)
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": progress_pct,
            "msg": f"训练 epoch {current_epoch}/{total_epochs}",
            "total_epochs": total_epochs,
            "current_epoch": current_epoch,
            **{f"train_{k}": v for k, v in metrics.items()
               if isinstance(v, (int, float))},
            **sticky_meta,
        })
        # 推历史曲线 (Redis + DB)
        TrainingLifecycleService.push_history(
            task_id, list(history_buffer),
            job_id=job_id,
            progress=progress_pct,
            message=f"训练 epoch {current_epoch}/{total_epochs}",
            current_epoch=current_epoch,
        )

    # ---- 3) 导 YOLO 数据集 ----
    try:
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            "progress": 1.0,
            "msg": "正在导出 YOLO 数据集...",
            "total_epochs": epochs,
        })

        async def _export():
            from app.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                return await export_yolo_dataset(
                    db=db, dataset_id=dataset_id,
                    workdir=workdir, val_ratio=val_ratio,
                    progress_cb=_export_cb,
                )

        export_info = _run_async(_export())
        sticky_meta["data_total"] = export_info["train_count"] + export_info["val_count"]
        sticky_meta["data_train"] = export_info["train_count"]
        sticky_meta["data_val"] = export_info["val_count"]
        sticky_meta["num_classes"] = len(export_info["classes"])
        sticky_meta["class_names"] = export_info["classes"]
        # 跨函数透传 (失败路径也能拿到)
        TrainingLifecycleService.set_last_sticky_meta(task_id, sticky_meta)

        # 数据集统计写库 + 推送
        TrainingLifecycleService.set_task_state(self, "PROGRESS", {
            **sticky_meta,
            "progress": 5.0,
            "msg": f"数据集就绪: train={export_info['train_count']} val={export_info['val_count']}",
            "total_epochs": epochs,
        })
        TrainingLifecycleService.persist_dataset_stats_sync(task_id, sticky_meta)

        # ---- 4) 跑训练 (纯 ML) ----
        result = train_yolo(
            data_yaml=export_info["data_yaml"],
            model_name=f"{model_name}.pt",
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            project=str(settings.MODEL_DIR / "runs"),
            name=model_alias,
            progress_cb=_train_cb,
        )

        # ---- 5) 写 ModelVersion + TrainingJob SUCCESS (委托 Service) ----
        mv_id = TrainingLifecycleService.create_model_version_sync(
            name=model_alias,
            base_model=model_name,
            dataset_id=dataset_id,
            task_type="detection",
            num_classes=len(export_info["classes"]),
            file_path=result["best_pt"],
            metrics=result["metrics"],
            history=history_buffer,
        )
        TrainingLifecycleService.mark_success_sync(
            job_id=job_id,
            started_at=started_at,
            history_buffer=history_buffer,
            message=f"训练完成 mAP50={result['metrics'].get('map_50', 0):.4f}",
            model_version_id=mv_id,
            sticky_meta=sticky_meta,
        )
        return {
            "status": "SUCCESS", "job_id": job_id, "model_version_id": mv_id,
            "best_pt": result["best_pt"], "metrics": result["metrics"],
        }

    except YoloTrainError as e:
        _finish_failed(self, job_id, e, started_at, task_id)
        return {"status": "FAILURE", "job_id": job_id, "error": str(e)[:500]}
    except Exception as e:
        _finish_failed(self, job_id, e, started_at, task_id)
        return {"status": "FAILURE", "job_id": job_id, "error": str(e)[:500]}
    finally:
        # 清理临时数据集导出目录
        shutil.rmtree(workdir, ignore_errors=True)


def _finish_failed(self, job_id: int, exc: Exception, started_at: datetime, task_id: str):
    """训练失败统一清理 (Phase 5: 委托 TrainingLifecycleService)"""
    from app.tasks.service.training_lifecycle_service import TrainingLifecycleService

    sticky_meta = TrainingLifecycleService.get_last_sticky_meta(task_id)
    TrainingLifecycleService.mark_failure_sync(
        job_id=job_id,
        error=str(exc),
        started_at=started_at,
        exc_type=type(exc).__name__,
        sticky_meta=sticky_meta,
    )
    TrainingLifecycleService.set_task_state(self, "FAILURE", {
        "exc_type": type(exc).__name__,
        "exc_message": str(exc)[:200],
        "error": str(exc)[:500],
        "job_id": job_id,
    })
