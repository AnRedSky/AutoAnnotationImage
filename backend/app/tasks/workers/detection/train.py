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
    pretrained_model_path: Optional[str] = None,
):
    """异步 YOLOv8 训练 (Phase 5: 编排下沉到 TrainingLifecycleService)

    v3.6.2: 新增 pretrained_model_path 参数, 断点续训用
    - None: 走 ultralytics 内置预训练权重 (yolov8n.pt 等)
    - 已存在路径: 加载该 .pt 作为模型起点, train(resume=True) 续训
      (ultralytics 会同时恢复 optimizer / scheduler / epoch 计数)
    """
    from app.tasks.ml.detection import export_yolo_dataset, train_yolo, YoloTrainError
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

    # v3.5.0: 启动时清掉历史 pause/cancel 残留 (避免上次异常退出时残留的信号误触发)
    clear_control_signals(task_id)
    # v3.5.0: 工厂方法生成 pause_check 回调 — 在每个 epoch 结束检查暂停/取消信号
    _pause_check_factory = make_pause_check(task_id)

    # v3.0.0: 采集训练设备信息 (CPU/GPU/CUDA/显存), 塞入 sticky_meta 供 mark_success 持久化
    # - 尽早采集, 失败路径也能通过 sticky_meta 记录运行设备
    try:
        from app.tasks.ml.device_info import collect_device_info, extract_job_device_fields
        _device_info = collect_device_info()
        sticky_meta.update(extract_job_device_fields(_device_info))
        # 立即透传, 确保导出阶段失败时 _finish_failed 也能拿到设备信息
        TrainingLifecycleService.set_last_sticky_meta(task_id, sticky_meta)
    except Exception:
        pass

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
        epoch_msg = f"训练 epoch {current_epoch}/{total_epochs}"
        # v3.5.0 Phase T7 #8: 合并 commit (log + progress + current_epoch + history)
        TrainingLifecycleService.set_task_state(
            self, "PROGRESS", {
                "progress": progress_pct,
                "msg": epoch_msg,
                "total_epochs": total_epochs,
                "current_epoch": current_epoch,
                **{f"train_{k}": v for k, v in metrics.items()
                   if isinstance(v, (int, float))},
                **sticky_meta,
            },
            commit_progress=progress_pct,
            commit_message=epoch_msg,
            commit_current_epoch=current_epoch,
            commit_history=list(history_buffer),
        )
        # 推历史曲线 (RPUSH 到 Redis, commit_db=False 避免重复写 DB)
        TrainingLifecycleService.push_history(
            task_id, list(history_buffer),
            job_id=job_id,
            progress=progress_pct,
            message=epoch_msg,
            current_epoch=current_epoch,
            commit_db=False,  # Phase T7 #8: 已合并到 set_task_state
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
        TrainingLifecycleService.persist_dataset_stats_sync(task_id, sticky_meta, job_id=job_id)

        # ---- 4) 跑训练 (纯 ML) ----
        result = train_yolo(
            data_yaml=export_info["data_yaml"],
            model_name=f"{model_name}.pt",
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            # v3.3.0: YOLO run 目录从 settings.MODEL_DIR/runs 改到 settings.DETECTION_MODEL_DIR.
            #         之前的 runs/ 与 cache/ultralytics/runs 分散两个地方, 现在统一收纳到 detection/.
            project=str(settings.DETECTION_MODEL_DIR),
            name=model_alias,
            progress_cb=_train_cb,
            # v3.5.0: 注入 pause_check 回调, 让 train_yolo 每个 epoch 结束检查
            # 暂停/取消信号 (SignalAction 枚举)
            pause_check=_pause_check_factory,
            # v3.6.2: 断点续训 — pretrained_model_path 非空时, ultralytics 用
            #   model.train(resume=True) 续训 (含 optimizer/scheduler/epoch 状态)
            pretrained_model_path=pretrained_model_path,
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

    except TaskCanceled as tc:
        # v3.5.0: 用户主动取消 (TaskCanceled 异常来自 train_yolo 的 pause_check 回调)
        # 走 mark_canceled_sync 写 CANCELED 状态 + 清理半成品 ModelVersion
        # 与 classification 路径对称, 保证状态机一致
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
        # v3.5.0: 用户主动暂停 (TrainingPaused 异常来自 train_yolo 的 pause_check 回调)
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
