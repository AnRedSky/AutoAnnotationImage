"""
Celery Tasks: Model Training & Auto Annotate (v3.0.0 Phase 5 薄化)
===========================================
真正的训练逻辑 / 异步预标注, 在 Celery worker 中执行

**v3.0.0 Phase 5 重构**:
- 业务编排 (TrainingJob 状态机 / sticky_meta / 历史推送 / 失败清理) 全部下沉到
  TrainingLifecycleService, worker 主体从 ~585 行减到 ~300 行
- 兼容垫片 `_update_training_history` / `_persist_dataset_stats` 移至 TrainingLifecycleService
"""
import hashlib
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
                     pretrained_model_path: Optional[str] = None,
                     resume_from_epoch: int = 0):
    """
    异步训练任务 (Phase 5: 委托 TrainingLifecycleService)
    - 加载已确认/修正的图片 + 标签
    - 划分 train/val
    - 训练 N 个 epoch
    - 实时更新 progress
    - 保存最佳模型 + 评估指标
    - 写入 TrainingJob 任务历史

    v3.6.3: 新增 resume_from_epoch 参数 (断点续训用, 跳过前 N 个 epoch)
    - 0 (默认): 全新训练
    - >0:       断点续训, 从该 epoch (0-based) 开始
    - 配套: pretrained_model_path 需非空 (否则无 checkpoint 可用)
    """
    from app.tasks.ml.classification import run_training, TrainingPaused
    from app.tasks.service.training_data_service import TrainingDataService
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
        """每 batch 进度回调

        v3.5.0 Phase T7 #6 优化: 移除 class_names 全量 push
        原代码: extra 携带 class_names (50-200 项 List[str], 1-10 KB), 每 batch 进 sticky_meta
        → SSE 客户端每 1s 1 帧 (PROGRESS) 全量重发 + Redis 缓存反复序列化
        → 客户端 store 反复 reactive 触发
        修复: class_names 是**慢变化数据**, 训练启动时一次性写 DB + 透传即可
              progress_callback 阶段只传 num_classes (int, 几个字节)

        v3.6.0 P5: sticky_meta 缓存 class_names hash, 避免重复写 DB
        - 原: 每次 progress_callback 含 data_total+num_classes 时都调 persist_dataset_stats_sync
              写库 (实际业务只调一次, 但代码无防护, 重构后可能引入回归)
        - 优: 缓存上次写入的 class_names hash, 比较后再决定
              同一数据集反复训练, dataset_stats DB 写为 0 次
        """
        meta = {
            "progress": round(p, 2),
            "msg": msg,
            "total_epochs": total_epochs,
        }
        if sticky_meta:
            meta.update(sticky_meta)
        if extra:
            # v3.5.0 Phase T7 #6: 过滤 class_names (初始化时已传, 训练中不再重发)
            _extra = {k: v for k, v in extra.items() if k != "class_names"}
            if _extra:
                meta.update(_extra)
                sticky_meta.update(_extra)
        TrainingLifecycleService.set_task_state(self, "PROGRESS", meta)
        # 持久化数据集统计到 DB (仅当 class_names 变化时才写)
        if extra and "data_total" in extra and "num_classes" in extra:
            _class_names = extra.get("class_names") or []
            # v3.6.0 P5: hash 缓存, 同 class_names 不重复写 DB
            _cn_hash = hashlib.md5(
                json.dumps(_class_names, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if sticky_meta.get("class_names_hash") != _cn_hash:
                sticky_meta["class_names_hash"] = _cn_hash
                TrainingLifecycleService.persist_dataset_stats_sync(
                    task_id, extra, job_id=job_id,
                )

    def epoch_cb(p: float, msg: str, epoch_data: dict):
        """每 epoch 结束回调

        v3.5.0 Phase T7 #8 优化: 合并 DB write
        - 原: set_task_state (写 log 行) + push_history (写 progress/history) = 2 次 commit / epoch
        - 优: set_task_state 合并 commit (log + progress + current_epoch + history) = 1 次 commit,
              push_history(commit_db=False) 仅 RPUSH 到 Redis
        """
        history_buffer.append(epoch_data)
        epoch_num = epoch_data.get("epoch")
        meta = {
            "progress": round(p, 2),
            "msg": msg,
            "total_epochs": total_epochs,
            "epoch": epoch_num,
            "current_epoch": epoch_num,
            "val_acc": epoch_data.get("val_acc"),
            "train_loss": epoch_data.get("train_loss"),
            "val_loss": epoch_data.get("val_loss"),
        }
        if sticky_meta:
            meta.update(sticky_meta)
        # v3.5.0 Phase T7 #8: 一次性 commit log + progress + current_epoch + history
        TrainingLifecycleService.set_task_state(
            self, "PROGRESS", meta,
            commit_progress=p,
            commit_message=msg,
            commit_current_epoch=epoch_num,
            commit_history=list(history_buffer),
        )
        # 推历史曲线 (RPUSH 到 Redis, commit_db=False 避免重复写 DB)
        TrainingLifecycleService.push_history(
            task_id, list(history_buffer),
            job_id=job_id,
            progress=p,
            message=msg,
            current_epoch=epoch_num,
            commit_db=False,  # Phase T7 #8: 已合并到 set_task_state
        )

    # v3.5.0: 工厂方法生成 pause_check 回调 (避免内层函数捕获 task_id 变量)
    # 必须先于 pause_check 定义 — Python 按定义顺序解析闭包
    _pause_check_factory = make_pause_check(task_id)

    def pause_check() -> SignalAction:
        """每个 epoch 起点检查 Redis 暂停/取消信号 (v3.5.0 改: 返回 SignalAction)

        走 control_signals.make_pause_check, 统一与 detection/segmentation 对齐.
        - cancel 优先于 pause (cancel 标志存在 → 返回 CANCEL)
        - 读 Redis 失败优雅降级为 CONTINUE (不阻塞训练)
        """
        return _pause_check_factory()

    # 启动时清掉历史 pause/cancel 残留 (避免上次异常退出时残留的信号误触发)
    # 注: clear_all 内部已 try/except, 失败不抛
    clear_control_signals(task_id)

    try:
        # v3.6.4 HOTFIX: resume 模式加载已保存的历史曲线
        # - 场景: 暂停 → 继续训练, 新 train_model_task 启动后 history_buffer = []
        #         新 epoch_cb 调 set_task_state(commit_history=...) 会**覆盖** DB 中
        #         mark_paused 时保存的旧 history, 详情页曲线只显示 resume 后的数据
        # - 修复: worker 启动时, 显式从 DB 读出旧 history 预填到 history_buffer
        # - 自动判断: 不依赖 mode 参数, 只要 job.history 非空就视为续训场景
        #   (restart 模式 job 是新建的, history 必然为空, 不会误加载)
        prior_history = TrainingLifecycleService.get_job_history_sync(job_id)
        history_buffer: list = list(prior_history) if prior_history else []
        if prior_history:
            import logging as _cls_resume_log
            _cls_resume_log.getLogger(__name__).info(
                f"v3.6.4: classification resume 加载历史曲线, "
                f"{len(prior_history)} 个 epoch (从 epoch {resume_from_epoch} 续训)"
            )

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
            early_stop_patience=settings.EARLY_STOP_PATIENCE,
            pause_check=pause_check,
            pretrained_model_path=pretrained_model_path,
            # v3.6.3: 断点续训起始 epoch (0-based), 与 pretrained_model_path 配套
            # resume 时模型从 checkpoint 加载, 然后从 start_epoch 处继续训练
            # v3.6.3.1 HOTFIX: 之前写错为 resume_from_epoch=..., run_training 实际签名是 start_epoch=
            #   → TypeError: run_training() got an unexpected keyword argument 'resume_from_epoch'
            #   → 与 segmentation/detection worker 对齐: worker 入参 resume_from_epoch (业务语义)
            #     透传到 ML 层时改名为 start_epoch (ML 层语义)
            start_epoch=resume_from_epoch,
            data_loader=TrainingDataService.load_classification_samples_sync,
            model_saver=TrainingDataService.save_classification_model_version_sync,
        )

        # ---- 2) TrainingJob SUCCESS (委托 Service) ----
        # v3.0.0: ModelVersion 已在 run_training → model_saver 内部创建完成 (含
        # accuracy/precision/recall/f1_score/confusion_matrix/training_log/class_names),
        # 这里直接复用 result["model_version_id"], 不再二次创建空记录 (旧实现会插入
        # num_classes=0 metrics={} 的脏行, 导致前端详情页拉到空指标).
        mv_id = result.get("model_version_id")

        # v3.0.0: 不合格虚拟类别训练元信息透传给前端 (sticky_meta 持久化到 SSE)
        # - unqualified_count: 纳入训练的不合格样本数 (0=未启用或降级)
        # - unqualified_skipped: True=样本不足自动降级 (跳过不合格类别)
        # - unqualified_included: 是否含虚拟 __unqualified__ 类别
        # - unqualified_warning: 软门禁警告 (None=达标 / 字符串=不达标, 前端 SSE 显示)
        uq_count = result.get("unqualified_count", 0)
        uq_skipped = result.get("unqualified_skipped", False)
        uq_class_names = result.get("class_names") or []
        uq_warning = result.get("unqualified_warning")
        sticky_meta["unqualified_count"] = int(uq_count)
        sticky_meta["unqualified_skipped"] = bool(uq_skipped)
        sticky_meta["unqualified_included"] = (
            TrainingDataService.UNQUALIFIED_LABEL in uq_class_names
        )
        if uq_warning:
            sticky_meta["unqualified_warning"] = str(uq_warning)[:500]

        # v3.0.0: 训练设备信息透传给 mark_success 持久化到 TrainingJob
        # - run_training 已调 collect_device_info() 采集, 返回值带 device_type/device_info
        # - final_device_info["gpu_peak_mb"] 是 CUDA 训练过程中的真实峰值显存
        result_device_info = result.get("device_info") or {}
        if result.get("device_type"):
            sticky_meta["device_type"] = result["device_type"]
            sticky_meta["device_name"] = result_device_info.get("device_name", "CPU")
            sticky_meta["device_info"] = result_device_info
            gpu_peak = result_device_info.get("gpu_peak_mb")
            if gpu_peak is not None:
                sticky_meta["gpu_peak_memory_mb"] = int(gpu_peak)

        # v3.0.0: 早停信息透传 (前端详情页可显示「实际跑 X/Y 轮, 触发早停」)
        if result.get("early_stopped"):
            sticky_meta["early_stopped"] = True
            sticky_meta["actual_epochs"] = int(result.get("actual_epochs", 0))
            sticky_meta["configured_epochs"] = int(result.get("configured_epochs", 0))
        # v3.0.0: message 携带早停提示, 让前端 status 描述直观可见
        success_message = "Training completed"
        if result.get("early_stopped"):
            ae = int(result.get("actual_epochs", 0))
            ce = int(result.get("configured_epochs", 0))
            success_message = f"训练完成 (早停: {ae}/{ce} 轮)"

        TrainingLifecycleService.mark_success_sync(
            job_id=job_id,
            started_at=started_at,
            history_buffer=history_buffer,
            message=success_message,
            model_version_id=mv_id,
            sticky_meta=sticky_meta,
        )
        return {"status": "SUCCESS", "result": result, "job_id": job_id}

    except TaskCanceled as tc:
        # 用户主动取消 (v3.5.0 新增) — 委托 Service 写 CANCELED 状态
        # 区别于 TrainingPaused: CANCELED 是终态, 不可"继续", 用户必须"再训练"
        try:
            redis_client.delete(f"train:cancel:{task_id}")
        except Exception:
            pass
        TrainingLifecycleService.mark_canceled_sync(
            job_id=job_id,
            started_at=started_at,
            epoch=tc.epoch,
            total_epochs=tc.total_epochs,
            history_buffer=history_buffer,
            model_name=model_name,
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
                json.dumps({
                    "error": str(e)[:500],
                    "progress": 0.0,
                    "status": "FAILURE",
                    # v3.5.0: 显式带 exc_type/exc_message, 与 Celery meta 字段对齐
                    # 便于前端 / 错误 API 读取时不需要靠 error 字符串反推
                    "exc_type": type(e).__name__,
                    "exc_message": str(e)[:200],
                }),
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
    # v3.3.6-STATS-ISOLATION: 把 user_id 和 dataset_id 写入 meta,
    # 供 /api/auto-annotate/status/{task_id} 端点做权限校验
    TrainingLifecycleService.set_task_state(self, "PROGRESS", {
        "progress": 0, "msg": "Loading model...",
        "user_id": user_id, "dataset_id": dataset_id,
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
