"""
Celery Tasks: Model Training & Auto Annotate
===========================================
真正的训练逻辑 / 异步预标注, 在 Celery worker 中执行
"""
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.workers.celery_app import celery_app
from app.core.redis_client import redis_client

# ---- 在最早期禁用 HF symlink (Windows [WinError 14007] 根因) ----
# Celery worker 进程不经过 FastAPI startup, 所以必须在 worker import 阶段
# 就把这两个开关写进 os.environ + huggingface_hub.constants, 否则后面
# import timm 仍会触发 symlink 失败。
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
try:
    import huggingface_hub.constants as _hf_const
    _hf_const.HF_HUB_DISABLE_SYMLINKS = True
    _hf_const.HF_HUB_DISABLE_SYMLINKS_WARNING = True
    # 让 worker 也走国内镜像
    from app.config import settings as _settings
    if _settings.HF_ENDPOINT:
        _hf_const.HF_ENDPOINT = _settings.HF_ENDPOINT
        _hf_const.HUGGINGFACE_HUB_ENDPOINT = _settings.HUGGINGFACE_HUB_ENDPOINT
        os.environ.setdefault("HF_ENDPOINT", _settings.HF_ENDPOINT)
        os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", _settings.HUGGINGFACE_HUB_ENDPOINT)
except Exception:
    pass


def _run_async(coro):
    """
    在 fresh event loop 中跑 coroutine, 并 dispose engine 防止
    SQLAlchemy async 连接池绑到已关闭的 loop 上。

    背景: Celery 任务是 sync 函数, 内部用 asyncio.run() 调 async DB 代码。
    多次 asyncio.run() 会反复创建/关闭 event loop, 但 SQLAlchemy async engine
    的连接池会缓存 loop 引用, 第二次调用就会出现 "Event loop is closed" /
    "NoneType has no attribute send" 错误 (greenlet bridge 失败)。
    每次 dispose engine 可以强制清空 pool, 重建时绑到新 loop 上。
    """
    from app.database import engine

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        # 清理旧 loop 残留的连接
        try:
            loop.run_until_complete(engine.dispose())
        except Exception:
            pass
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass


def _update_training_history(task_id: str, history: list):
    """训练历史曲线写入 Redis, 前端可轮询 /training/history/{task_id} 获取"""
    key = f"train:history:{task_id}"
    redis_client.setex(key, 86400, json.dumps(history))  # 24h 过期


@celery_app.task(bind=True)
def train_model_task(self, dataset_id: int, base_model: str, model_name: str,
                     user_id: int, epochs: int = 20, batch_size: int = 32,
                     learning_rate: float = 1e-4,
                     pretrained_model_path: Optional[str] = None):
    """
    异步训练任务
    - 加载已确认/修正的图片 + 标签
    - 划分 train/val
    - 训练 N 个 epoch
    - 实时更新 progress (前端可轮询)
    - 保存最佳模型 + 评估指标
    - 写入 TrainingJob 任务历史

    pretrained_model_path: 增量训练 (再训练) 时, 加载 .pth 权重作为模型起点;
        None 表示从头微调 (timm ImageNet 预训练权重)
    """
    from app.database import AsyncSessionLocal
    from app.models.training_job import TrainingJob

    task_id = self.request.id
    started_at = datetime.utcnow()
    total_epochs = epochs  # 用于 progress meta 携带

    # 1. 写 TrainingJob (PENDING -> PROGRESS)
    # 使用 upsert 模式: 如果已有同名 celery_task_id 的 job (worker 崩溃后重投递场景
    # 或 API 端预创建的场景, 见 api/training.py:855-899), 直接复用并 reset 状态
    # 避免 IntegrityError 阻塞后续流程.
    async def _create_job():
        from sqlalchemy import select
        from sqlalchemy.dialects.mysql import insert as mysql_insert
        async with AsyncSessionLocal() as db:
            # 先查是否已存在 (re-delivery / API 预创建场景)
            existing = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if existing is not None:
                # ---- 区分"API 预创建"和"worker 重投递"两种场景 ----
                # - API 预创建: row.message == "等待 worker 启动..." 或 "任务已入队, 等待 worker 启动..."
                #   这是首次启动, 沿用原 message 不替换 (避免覆盖前端展示)
                # - worker 重投递 (崩溃恢复): row.message != 预创建文案
                #   此时打上 "Re-running" 标识, 方便排查
                is_api_precreated = existing.message in (
                    "等待 worker 启动...",
                    "任务已入队, 等待 worker 启动...",
                )
                existing.state = "PROGRESS"
                existing.progress = 0.0
                existing.error = None
                if not is_api_precreated:
                    existing.message = "Re-running (worker restart recovery)"
                existing.started_at = started_at
                existing.finished_at = None
                existing.duration_seconds = None
                await db.commit()
                await db.refresh(existing)
                return existing.id
            # 不存在则 INSERT (用 IGNORE 防止极端并发)
            stmt = mysql_insert(TrainingJob).values(
                celery_task_id=task_id,
                user_id=user_id,
                dataset_id=dataset_id,
                base_model=base_model,
                model_name=model_name,
                epochs=epochs,
                batch_size=batch_size,
                learning_rate=learning_rate,
                state="PROGRESS",
                progress=0.0,
                started_at=started_at,
            )
            stmt = stmt.on_duplicate_key_update(
                state="PROGRESS", progress=0.0, started_at=started_at,
            )
            await db.execute(stmt)
            await db.commit()
            # 拿回 id
            job = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one()
            return job.id

    job_id = _run_async(_create_job())
    history_buffer = []  # 累积训练曲线

    # 累积 meta (数据集统计 / 增量训练状态只在启动那一刻推一次, 后续 epoch_cb 会
    # 覆盖 meta, 所以用 sticky_meta 保留这些「永久字段」避免丢失)
    sticky_meta: dict = {}

    def progress_cb(p: float, msg: str, extra: dict = None):
        """每 batch 进度回调 (每 100 batch 调用一次, 由 train.py 控制)"""
        meta = {
            "progress": round(p, 2),
            "msg": msg,
            "total_epochs": total_epochs,
        }
        # 先合并 sticky (数据统计 + 增量训练状态, 不会随 epoch 进度丢失)
        if sticky_meta:
            meta.update(sticky_meta)
        if extra:
            meta.update(extra)
            # 一次性字段 (data_total/data_train/.../pretrained_loaded/...) 推过后
            # 永久保留, 后续 epoch 回调也能透传给前端
            sticky_meta.update(extra)
        self.update_state(state="PROGRESS", meta=meta)

    def epoch_cb(p: float, msg: str, epoch_data: dict):
        """每 epoch 结束回调, 累积历史并写入 Redis"""
        history_buffer.append(epoch_data)
        _update_training_history(task_id, history_buffer)
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
        # 关键: 永远把 sticky_meta 合并进来 (数据集统计 + 增量训练状态)
        if sticky_meta:
            meta.update(sticky_meta)
        self.update_state(state="PROGRESS", meta=meta)

    def pause_check() -> bool:
        """每个 epoch 起点被 train.py 调用, 检查 Redis 暂停标志"""
        try:
            return redis_client.get(f"train:pause:{task_id}") is not None
        except Exception:
            return False

    # 启动时清掉旧暂停标志 (避免重投递 / 复用 task_id 误判)
    try:
        redis_client.delete(f"train:pause:{task_id}")
    except Exception:
        pass

    try:
        from app.ml.train import run_training, TrainingPaused
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
        )

        # 2. 更新 TrainingJob (SUCCESS)
        async def _finish_job():
            from sqlalchemy import select
            async with AsyncSessionLocal() as db:
                job = await db.get(TrainingJob, job_id)
                if not job:
                    return
                job.state = "SUCCESS"
                job.progress = 100.0
                job.message = "Training completed"
                job.finished_at = datetime.utcnow()
                job.duration_seconds = (job.finished_at - started_at).total_seconds()
                job.history = history_buffer
                # ---- 训练资源记录: 把 train.py 采集的设备信息写库 ----
                # result["device_type"] = "cuda" / "mps" / "cpu"
                # result["device_info"] = {device_name, cuda_version, ...}
                dv_type = result.get("device_type")
                if dv_type:
                    job.device_type = str(dv_type)[:16]
                dv_info = result.get("device_info")
                if isinstance(dv_info, dict):
                    job.device_info = dv_info
                    # 单独提取几个便于快速过滤/显示的字段
                    if not job.device_name and dv_info.get("device_name"):
                        job.device_name = str(dv_info["device_name"])[:128]
                    peak = dv_info.get("gpu_peak_mb")
                    if isinstance(peak, (int, float)):
                        job.gpu_peak_memory_mb = int(peak)
                # 关联 ModelVersion
                if result.get("model_path"):
                    from app.models.model_version import ModelVersion
                    # 取最新一条 (同 file_path 可能有多个 v5_xxx 旧记录, 取 id 最大)
                    stmt = (
                        select(ModelVersion)
                        .where(ModelVersion.file_path == result["model_path"])
                        .order_by(ModelVersion.id.desc())
                    )
                    mv = (await db.execute(stmt)).scalars().first()
                    if mv:
                        job.model_version_id = mv.id
                await db.commit()

        _run_async(_finish_job())
        return {"status": "SUCCESS", "result": result, "job_id": job_id}

    except TrainingPaused as tp:
        # 用户主动暂停: 写 DB PAUSED 状态 + 清 Redis 暂停标志 + 清盘
        # 走 update_state(REVOKED) 让 Celery 端状态正确 (避免 PAUSED 非标准状态触发
        # 序列化的未知坑); UI 通过 SSE 拿到 DB 真实状态 PAUSED
        try:
            redis_client.delete(f"train:pause:{task_id}")
        except Exception:
            pass
        # 清理半成品: 与 FAILURE 路径同, 但保留 history_buffer
        from sqlalchemy import select, delete
        from app.models.model_version import ModelVersion

        async def _pause_and_cleanup():
            async with AsyncSessionLocal() as db:
                try:
                    await db.execute(
                        delete(ModelVersion).where(
                            ModelVersion.name == model_name,
                            ModelVersion.is_active == False,  # noqa: E712
                        )
                    )
                    await db.commit()
                except Exception:
                    pass
                pth_path = Path(__file__).parent.parent / "models" / f"{model_name}_best.pth"
                if pth_path.exists():
                    try: pth_path.unlink()
                    except OSError: pass
                try:
                    job = await db.get(TrainingJob, job_id)
                    if job:
                        job.state = "PAUSED"
                        job.progress = round(tp.epoch / max(tp.total_epochs, 1) * 100, 2)
                        job.message = f"Paused at epoch {tp.epoch}/{tp.total_epochs}"
                        job.finished_at = datetime.utcnow()
                        job.duration_seconds = (job.finished_at - started_at).total_seconds()
                        job.history = history_buffer
                        await db.commit()
                except Exception:
                    pass

        try: _run_async(_pause_and_cleanup())
        except Exception: pass

        try:
            self.update_state(
                state="REVOKED",
                meta={
                    "progress": round(tp.epoch / max(tp.total_epochs, 1) * 100, 2),
                    "msg": f"Paused at epoch {tp.epoch}/{tp.total_epochs}",
                    "epoch": tp.epoch,
                    "total_epochs": tp.total_epochs,
                    "job_id": job_id,
                },
            )
        except Exception:
            pass
        return None

    except Exception as e:
        # 3. 异常时清理半成品 (ModelVersion 记录 + 磁盘 .pth 文件)
        from sqlalchemy import select, delete
        from app.models.model_version import ModelVersion
        from sqlalchemy.exc import IntegrityError

        async def _fail_and_cleanup():
            async with AsyncSessionLocal() as db:
                # 3.1 删半成品 ModelVersion (与本次任务同 model_name 的非激活记录)
                try:
                    await db.execute(
                        delete(ModelVersion).where(
                            ModelVersion.name == model_name,
                            ModelVersion.is_active == False,  # noqa: E712
                        )
                    )
                    await db.commit()
                except Exception:
                    pass

                # 3.2 删磁盘上半成品 .pth
                pth_path = Path(__file__).parent.parent / "models" / f"{model_name}_best.pth"
                if pth_path.exists():
                    try:
                        pth_path.unlink()
                    except OSError:
                        pass

                # 3.3 TrainingJob 状态置 FAILED
                try:
                    job = await db.get(TrainingJob, job_id)
                    if job:
                        job.state = "FAILURE"
                        job.error = str(e)[:500]
                        job.finished_at = datetime.utcnow()
                        job.duration_seconds = (job.finished_at - started_at).total_seconds()
                        await db.commit()
                except Exception:
                    pass

        try:
            _run_async(_fail_and_cleanup())
        except Exception:
            pass

        # 关键: 不能 raise (raise 会让 Celery 内部 mark_as_failure 链路也未必安全;
        # 且 self.request.retries 等机制会变复杂); 也不能 return dict (Celery 会把它当
        # result 存进 Redis 标 SUCCESS, 训练实际失败但前端看到 SUCCESS).
        # 正确做法: 调 self.update_state(state='FAILURE', meta={..., exc_type, exc_message})
        # 让 Celery 标 task state=FAILURE. meta 里必须带 exc_type (Celery 的 _store_result
        # 校验 FAILURE state 时会检查, 缺了直接抛 ValueError "Exception information must
        # include the exception type", 这个异常会污染 worker 让其退出, 是 7 月初线上多次
        # 假失败的根因).
        # DB 状态已由 _fail_and_cleanup 写 FAILURE.
        # 错误详情单独存到 Redis 供前端读取.
        try:
            self.update_state(
                state="FAILURE",
                meta={
                    "exc_type": type(e).__name__,
                    "exc_message": str(e)[:200],
                    "progress": 0.0,
                    "msg": str(e)[:200],
                    "error": str(e)[:500],
                    "job_id": job_id,
                },
            )
        except Exception:
            pass
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
    异步 AI 预标注任务 (大批量)
    - 接收 category_names 列表 (前端传入或从 DB 兜底加载)
    - base model 输出过滤到只含项目类目, 无匹配则不标注
    - 返回 { total, auto_labeled, need_human, no_match }
    """
    from app.database import AsyncSessionLocal
    from app.models.image import Image
    from app.models.user import User
    from app.models.category import Category
    from app.models.annotation_log import AnnotationLog
    from app.services import ai_service
    from app.services.ai_service import filter_predictions_to_categories
    from app.config import settings
    from sqlalchemy import select

    task_id = self.request.id
    self.update_state(state="PROGRESS", meta={"progress": 0, "msg": "Loading model..."})

    async def _run():
        # category_names 由调用方传入; 兜底从 DB 读 (防止前端忘传)
        names = category_names
        if not names:
            async with AsyncSessionLocal() as _db:
                rows = (await _db.execute(
                    select(Category).where(Category.dataset_id == dataset_id)
                )).scalars().all()
                names = [c.name for c in rows]
        if not names:
            return {
                "total": 0, "auto_labeled": 0, "need_human": 0, "no_match": 0,
                "error": "数据集无预设类目"
            }

        # 加载模型
        if ai_service.current_model_name != model_name:
            await ai_service.load_pretrained(model_name)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Image).where(
                    Image.dataset_id == dataset_id,
                    Image.status == "pending",
                )
            )
            images = result.scalars().all()
            total = len(images)
            if total == 0:
                return {"total": 0, "auto_labeled": 0, "need_human": 0, "no_match": 0}

            storage_root = settings.UPLOAD_DIR
            image_paths = [str(storage_root / img.storage_path) for img in images]
            predictions = await ai_service.batch_predict(image_paths, top_k=5)

            # 关键: 过滤 base model 输出到项目类目
            filtered_preds = filter_predictions_to_categories(predictions, names)

            # 类目名 -> id 映射 (用于 ai_predict 审计)
            cat_rows = (await db.execute(
                select(Category).where(Category.dataset_id == dataset_id)
            )).scalars().all()
            name_to_id = {c.name: c.id for c in cat_rows}

            auto_labeled = 0
            no_match = 0
            for i, (img, fp) in enumerate(zip(images, filtered_preds)):
                if fp is None:
                    img.ai_prediction = None
                    no_match += 1
                else:
                    img.ai_prediction = fp
                    if fp["top1_conf"] >= confidence_threshold:
                        img.status = "ai_labeled"
                        # 写 ai_predict 审计
                        top1_label_name = fp.get("top1")
                        to_label_id = name_to_id.get(top1_label_name)
                        db.add(AnnotationLog(
                            image_id=img.id,
                            user_id=user_id,
                            action="ai_predict",
                            from_label_id=img.final_label_id,
                            to_label_id=to_label_id,
                            time_spent_ms=0,
                        ))
                        auto_labeled += 1
                # 每 10 张更新一次进度
                if (i + 1) % 10 == 0 or i == total - 1:
                    p = (i + 1) / total * 100
                    self.update_state(state="PROGRESS", meta={
                        "progress": p, "total": total,
                        "auto_labeled": auto_labeled,
                        "no_match": no_match,
                        "msg": f"Processed {i+1}/{total}"
                    })

            await db.commit()
            return {
                "total": total,
                "auto_labeled": auto_labeled,
                "need_human": total - auto_labeled,
                "no_match": no_match,
            }

    try:
        result = _run_async(_run())
        return {"status": "SUCCESS", **result}
    except Exception as e:
        # 同样不 raise, 避免 Celery 后端二次序列化失败
        # 关键: meta 必须带 exc_type, 否则 Celery 的 _store_result 会抛
        # "Exception information must include the exception type", 整个 worker 退出
        try:
            self.update_state(
                state="FAILURE",
                meta={
                    "exc_type": type(e).__name__,
                    "exc_message": str(e)[:200],
                    "error": str(e)[:500],
                },
            )
        except Exception:
            pass
        return {"status": "FAILURE", "error": str(e)[:500]}
