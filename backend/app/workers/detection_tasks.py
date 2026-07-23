"""
Celery Tasks: 目标检测训练 + 自动标注 (v2.0.0 S3.2)
======================================================

- train_detection_task:  训练 YOLOv8 (复用 S3.1 yolo_train.train_yolo)
- auto_annotate_detection_task: 用已训练模型批量预测, 结果入库 BBoxAnnotation

设计:
- 复用 tasks.py 的 _run_async 工具, 保持一致的事件循环/连接池管理
- 复用 TrainingJob ORM, 通过 task_type='detection' 区分 (S3.2 迁移新增列)
- 训练/推理失败按 Celery 标准做法: update_state(FAILURE) + meta 必带 exc_type
- auto_annotate 走 ai_service 风格: 写 ai_predict 审计 (虽然 detection 没
  final_label 字段, 用 status 字段表达 'ai_labeled' 状态)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.workers.celery_app import celery_app
from app.core.redis_client import redis_client
from app.core.celery_utils import run_async_in_worker as _run_async


# 早期: 与 tasks.py 同样的 HF symlink + 缓存目录兜底
# 在 import huggingface_hub / ultralytics 前设置预训练权重缓存目录
_model_dir_env = os.getenv("MODEL_DIR", "./models")
_cache_dir_env = os.getenv("PRETRAINED_CACHE_DIR", str(Path(_model_dir_env) / "cache"))
os.environ.setdefault("HF_HOME", str(Path(_cache_dir_env) / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(Path(_cache_dir_env) / "torch"))
os.environ.setdefault("ULTRALYTICS_HOME", str(Path(_cache_dir_env) / "ultralytics"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from app.config import settings


def _set_task_state(self, state: str, meta: dict):
    """统一的 Celery update_state, 失败/退出元数据自动加 exc_type (Celery 硬要求)"""
    if state == "FAILURE" and "exc_type" not in meta:
        meta["exc_type"] = "UnknownError"
    try:
        self.update_state(state=state, meta=meta)
    except Exception:
        pass


# ============== 任务 1: 训练 YOLOv8 ==============

@celery_app.task(bind=True)
def train_detection_task(
    self,
    dataset_id: int,
    user_id: int,
    model_name: str = "yolov8n",       # YOLOv8 预训练权重名 (不带 .pt)
    model_alias: str = "yolov8n_run",  # 落盘名 / ModelVersion.name
    epochs: int = 10,
    imgsz: int = 320,
    batch: int = 8,
    val_ratio: float = 0.2,
    device: str = "cpu",
):
    """
    异步 YOLOv8 训练

    Args:
        dataset_id: detection 数据集
        user_id: 发起人 (写入 TrainingJob.user_id)
        model_name: ultralytics 权重名 (yolov8n/s/m/l/x, 也可 .pt 绝对路径)
        model_alias: 本次训练的别名 (ModelVersion.name), 避免与基础权重名冲突
        epochs: 训练轮数
        imgsz: 输入尺寸
        batch: 批大小
        val_ratio: train/val 拆分比例
        device: cpu / cuda

    Returns:
        {status, job_id, best_pt, metrics}
    """
    from app.database import AsyncSessionLocal
    from app.models.training_job import TrainingJob
    from app.models.model_version import ModelVersion
    from sqlalchemy import select
    from app.ml.detection import (
        export_yolo_dataset, train_yolo, YoloTrainError,
    )

    task_id = self.request.id
    started_at = datetime.utcnow()

    # 1) 创建/复用 TrainingJob (task_type='detection')
    async def _create_job():
        async with AsyncSessionLocal() as db:
            existing = (await db.execute(
                select(TrainingJob).where(TrainingJob.celery_task_id == task_id)
            )).scalar_one_or_none()
            if existing is not None:
                existing.state = "PROGRESS"
                existing.progress = 0.0
                existing.error = None
                existing.started_at = started_at
                existing.finished_at = None
                existing.duration_seconds = None
                existing.task_type = "detection"
                # v2.5.28: 重投递 / API 预创建都重置数据集统计, 避免上一轮的
                # data_total/data_train/.../class_names 残留. 之前没清, 如果
                # 这次是新的数据集训练, 详情页"总样本数"会显示旧值, 用户困惑.
                existing.data_total = None
                existing.data_train = None
                existing.data_val = None
                existing.num_classes = None
                existing.class_names = None
                await db.commit()
                await db.refresh(existing)
                return existing.id
            job = TrainingJob(
                celery_task_id=task_id,
                user_id=user_id,
                dataset_id=dataset_id,
                base_model=model_name,
                model_name=model_alias,
                task_type="detection",
                epochs=epochs,
                batch_size=batch,
                learning_rate=0.0,  # YOLO 自带 lr 调度, 论文不显式
                state="PROGRESS",
                progress=0.0,
                started_at=started_at,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job.id

    job_id = _run_async(_create_job())
    history_buffer: list = []
    sticky_meta: dict = {}

    def export_cb(stage, current, total, info=""):
        meta = {
            "progress": round(current / max(total, 1) * 100, 2),
            "msg": f"[{stage}] {info}",
            "total_epochs": epochs,
        }
        if sticky_meta:
            meta.update(sticky_meta)
        _set_task_state(self, "PROGRESS", meta)

    def train_cb(stage, current_epoch, total_epochs, metrics):
        # 累积曲线
        history_buffer.append({
            "epoch": current_epoch,
            "total_epochs": total_epochs,
            **metrics,
        })
        meta = {
            "progress": round(current_epoch / max(total_epochs, 1) * 100, 2),
            "msg": f"训练 epoch {current_epoch}/{total_epochs}",
            "total_epochs": total_epochs,
            "current_epoch": current_epoch,
            **{f"train_{k}": v for k, v in metrics.items()
               if isinstance(v, (int, float))},
        }
        if sticky_meta:
            meta.update(sticky_meta)
        _set_task_state(self, "PROGRESS", meta)

    workdir = settings.DATA_DIR / "yolo" / f"{model_alias}_{task_id}"
    try:
        # 2) 导 YOLO 数据集
        _set_task_state(self, "PROGRESS", {
            "progress": 1.0,
            "msg": "正在导出 YOLO 数据集...",
            "total_epochs": epochs,
        })

        async def _export():
            async with AsyncSessionLocal() as db:
                return await export_yolo_dataset(
                    db=db, dataset_id=dataset_id,
                    workdir=workdir, val_ratio=val_ratio,
                    progress_cb=export_cb,
                )

        export_info = _run_async(_export())
        sticky_meta["data_total"] = export_info["train_count"] + export_info["val_count"]
        sticky_meta["data_train"] = export_info["train_count"]
        sticky_meta["data_val"] = export_info["val_count"]
        sticky_meta["num_classes"] = len(export_info["classes"])
        sticky_meta["class_names"] = export_info["classes"]
        # v2.5.28: 同步到模块全局, 失败路径 (_finish_failed_job) 也能读
        _LAST_STICKY_META["value"] = dict(sticky_meta)

        # 同步写库 (与 tasks.py / segmentation_tasks.py 风格一致, 即便失败不阻塞训练)
        _set_task_state(self, "PROGRESS", {
            **sticky_meta,
            "progress": 5.0,
            "msg": f"数据集就绪: train={export_info['train_count']} val={export_info['val_count']}",
            "total_epochs": epochs,
        })
        # v2.5.28 修复: 数据集就绪那一刻立即把统计写库, 失败路径也能保留
        # 之前: 只在 _finish_job 成功路径写, FAILURE 后 /jobs/{id} 返回的 ORM
        #       行 data_total/data_train/data_val/num_classes/class_names 全 None,
        #       详情页「总样本数 0 张 / 类别数 0 类」, 用户看不到数据集规模
        # 现在: 训练启动就绪那一刻 (不论后续成功失败) 把 5 个字段写库
        try:
            from app.workers.tasks import _persist_dataset_stats
            _persist_dataset_stats(task_id, sticky_meta)
        except Exception as e:
            # 写库失败不影响训练
            print(f"[warn] det _persist_dataset_stats failed: {type(e).__name__}: {e}")

        # 3) 跑训练
        result = train_yolo(
            data_yaml=export_info["data_yaml"],
            model_name=f"{model_name}.pt",
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            project=str(settings.MODEL_DIR / "runs"),
            name=model_alias,
            progress_cb=train_cb,
        )

        # 4) 写 ModelVersion
        async def _finish_job():
            async with AsyncSessionLocal() as db:
                mv = ModelVersion(
                    name=model_alias,
                    base_model=model_name,
                    dataset_id=dataset_id,
                    task_type="detection",
                    num_classes=len(export_info["classes"]),
                    file_path=result["best_pt"],
                    map_50=result["metrics"].get("map_50"),
                    map_50_95=result["metrics"].get("map_50_95"),
                    precision=result["metrics"].get("precision"),
                    recall=result["metrics"].get("recall"),
                    training_log={"history": history_buffer},
                    is_active=False,
                )
                db.add(mv)
                await db.commit()
                await db.refresh(mv)
                mv_id = mv.id

                job = await db.get(TrainingJob, job_id)
                if job:
                    job.state = "SUCCESS"
                    job.progress = 100.0
                    job.message = f"训练完成 mAP50={result['metrics'].get('map_50', 0):.4f}"
                    job.finished_at = datetime.utcnow()
                    job.duration_seconds = (job.finished_at - started_at).total_seconds()
                    job.model_version_id = mv_id
                    job.history = history_buffer
                    job.data_total = sticky_meta.get("data_total")
                    job.data_train = sticky_meta.get("data_train")
                    job.data_val = sticky_meta.get("data_val")
                    job.num_classes = sticky_meta.get("num_classes")
                    job.class_names = sticky_meta.get("class_names")
                    await db.commit()
                return mv_id

        mv_id = _run_async(_finish_job())
        return {
            "status": "SUCCESS", "job_id": job_id, "model_version_id": mv_id,
            "best_pt": result["best_pt"], "metrics": result["metrics"],
        }

    except YoloTrainError as e:
        # 业务失败
        _finish_failed_job(self, job_id, e, started_at)
        return {"status": "FAILURE", "job_id": job_id, "error": str(e)[:500]}

    except Exception as e:
        _finish_failed_job(self, job_id, e, started_at)
        return {"status": "FAILURE", "job_id": job_id, "error": str(e)[:500]}
    finally:
        # 训练后清理临时数据集导出目录 (images/labels/data.yaml)
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def _finish_failed_job(self, job_id: int, exc: Exception, started_at: datetime):
    """训练失败统一清理: 写 DB FAILURE + Celery update_state(FAILURE)

    v2.5.28 新增: 支持把已计算的 sticky_meta (数据集统计) 写库, 避免失败
    任务详情页显示全 0. 调用方可在调用前 set 一下 _last_sticky_meta 即可.
    """
    from app.database import AsyncSessionLocal
    from app.models.training_job import TrainingJob
    try:
        async def _fail():
            async with AsyncSessionLocal() as db:
                job = await db.get(TrainingJob, job_id)
                if job:
                    job.state = "FAILURE"
                    job.error = str(exc)[:500]
                    job.finished_at = datetime.utcnow()
                    job.duration_seconds = (job.finished_at - started_at).total_seconds()
                    # v2.5.28: 即便失败, 已计算的 sticky_meta (数据集统计) 也要写库
                    # 之前: 训练跑通 export_yolo_dataset 后才挂, 统计在 sticky_meta
                    #       里但没写库, _finish_failed_job 又只清 error/finished,
                    #       结果详情页「总样本数 / 类别数」显示空
                    # 现在: 从模块全局 _last_sticky_meta 拿 (worker 训练中赋值)
                    _sm = _LAST_STICKY_META.get("value")
                    if isinstance(_sm, dict) and _sm:
                        if "data_total" in _sm:
                            job.data_total = _sm["data_total"]
                        if "data_train" in _sm:
                            job.data_train = _sm["data_train"]
                        if "data_val" in _sm:
                            job.data_val = _sm["data_val"]
                        if "num_classes" in _sm:
                            job.num_classes = _sm["num_classes"]
                        if "class_names" in _sm:
                            job.class_names = _sm["class_names"]
                    await db.commit()
        _run_async(_fail())
    except Exception:
        pass
    _set_task_state(self, "FAILURE", {
        "exc_type": type(exc).__name__,
        "exc_message": str(exc)[:200],
        "error": str(exc)[:500],
        "job_id": job_id,
    })


# v2.5.28: 跨函数共享 sticky_meta (失败时 _finish_failed_job 也能拿到已计算的统计)
# 不放进 train_detection_task 闭包是因为 _finish_failed_job 是模块级函数, 无法
# 直接读 train_detection_task 局部变量. 用一个模块级 dict 透传, 简单够用.
_LAST_STICKY_META: dict = {}


# ============== 任务 2: 自动标注 (用已训练模型批量推理) ==============

@celery_app.task(bind=True)
def auto_annotate_detection_task(
    self,
    dataset_id: int,
    user_id: int,
    model_version_id: int,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    imgsz: int = 320,
    device: str = "cpu",
    overwrite_existing: bool = False,
):
    """
    用已训练好的 YOLOv8 模型批量预标注

    Args:
        dataset_id: 目标 detection 数据集
        user_id: 发起人
        model_version_id: ModelVersion.id (file_path 取自此)
        conf_threshold: 置信度阈值
        iou_threshold: NMS IoU 阈值
        imgsz: 推理输入尺寸
        device: cpu / cuda
        overwrite_existing: True 时覆盖已有 BBoxAnnotation

    Returns:
        {status, total, auto_labeled, no_match, error_msg}
    """
    from app.database import AsyncSessionLocal
    from app.models import Image as ImageModel
    from app.models.bbox_annotation import BBoxAnnotation
    from app.models.model_version import ModelVersion
    from app.models.annotation_log import AnnotationLog
    from app.models.category import Category
    from app.ml.detection import predict_image_grouped, YoloTrainError
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    task_id = self.request.id

    try:
        # 1) 拉 ModelVersion
        async def _load_mv():
            async with AsyncSessionLocal() as db:
                return await db.get(ModelVersion, model_version_id)
        mv = _run_async(_load_mv())
        if not mv or not mv.file_path or not Path(mv.file_path).exists():
            raise YoloTrainError(f"ModelVersion id={model_version_id} 权重不存在")

        # 2) 拉全部 detection 图
        async def _load_images() -> list:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(ImageModel)
                    .where(
                        ImageModel.dataset_id == dataset_id,
                        ImageModel.task_type == "detection",
                    )
                    .order_by(ImageModel.id.asc())
                )).scalars().all()
                # v2.5.30 修复: Image ORM 的字段是 storage_path, 不是 file_path
                # 之前 r.file_path 会抛 AttributeError: 'Image' object has no attribute 'file_path'
                return [(r.id, r.storage_path) for r in rows]
        items = _run_async(_load_images())
        if not items:
            return {"status": "SUCCESS", "total": 0, "auto_labeled": 0, "no_match": 0}

        # 3) 拉 Category (class_index → category_id)
        async def _load_cats() -> list:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(Category)
                    .where(Category.dataset_id == dataset_id)
                    .order_by(Category.id.asc())
                )).scalars().all()
                return list(rows)
        cats = _run_async(_load_cats())
        index_to_cat = {i: c for i, c in enumerate(cats)}

        # 4) 拼图片绝对路径 + 过滤存在的
        from app.services.storage_service import storage_service
        abs_paths = []
        valid_ids = []
        for img_id, fp in items:
            p = Path(fp)
            if not p.is_absolute():
                p = (Path(storage_service.base_path).resolve() / fp).resolve()
            if p.exists():
                abs_paths.append(str(p))
                valid_ids.append(img_id)

        # 5) 推理
        def _progress_cb(p, msg):
            _set_task_state(self, "PROGRESS", {
                "progress": round(p, 2),
                "msg": msg,
                "total": len(abs_paths),
            })

        grouped = predict_image_grouped(
            weights_path=mv.file_path,
            image_paths=abs_paths,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
            imgsz=imgsz,
            device=device,
        )

        # 6) 写 BBoxAnnotation
        async def _write_results():
            async with AsyncSessionLocal() as db:
                auto_labeled = 0
                no_match = 0
                for i, (img_id, p) in enumerate(zip(valid_ids, abs_paths)):
                    boxes = grouped.get(p, [])
                    if not boxes:
                        no_match += 1
                        continue
                    if overwrite_existing:
                        old = (await db.execute(
                            select(BBoxAnnotation)
                            .where(BBoxAnnotation.image_id == img_id)
                        )).scalars().all()
                        for o in old:
                            await db.delete(o)
                    for bb in boxes:
                        cat = index_to_cat.get(bb.class_index)
                        if cat is None:
                            continue
                        db.add(BBoxAnnotation(
                            image_id=img_id,
                            category_id=cat.id,
                            x_min=bb.x_min, y_min=bb.y_min,
                            x_max=bb.x_max, y_max=bb.y_max,
                            confidence=bb.confidence,
                            source="ai",
                            annotated_by=user_id,
                        ))
                        # 审计
                        db.add(AnnotationLog(
                            image_id=img_id, user_id=user_id,
                            action="ai_predict", time_spent_ms=0,
                        ))
                    auto_labeled += 1
                    if (i + 1) % 5 == 0 or i == len(valid_ids) - 1:
                        await db.commit()
                        _progress_cb(
                            (i + 1) / max(len(valid_ids), 1) * 100,
                            f"已标注 {i+1}/{len(valid_ids)}",
                        )
                return auto_labeled, no_match

        auto_labeled, no_match = _run_async(_write_results())
        return {
            "status": "SUCCESS",
            "total": len(valid_ids),
            "auto_labeled": auto_labeled,
            "no_match": no_match,
        }

    except Exception as e:
        _set_task_state(self, "FAILURE", {
            "exc_type": type(e).__name__,
            "exc_message": str(e)[:200],
            "error": str(e)[:500],
        })
        return {"status": "FAILURE", "error": str(e)[:500]}


# ============== v2.3.2: 用 ultralytics 预训练 yolov8n/s/m/l/x 做自动标注 ==============
# 不依赖 ModelVersion, 用户没训练模型也能用
PREDEFINED_YOLO_MODELS = {"yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x"}


@celery_app.task(bind=True, name="detection.auto_annotate_pretrained")
def auto_annotate_pretrained_task(
    self,
    dataset_id: int,
    user_id: int,
    model_name: str = "yolov8n",
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    imgsz: int = 320,
    device: str = "cpu",
    overwrite_existing: bool = False,
):
    """
    v2.3.2: 用 ultralytics 预训练 YOLOv8n/s/m/l/x 做 detection 数据集预标注
    与 auto_annotate_detection_task 区别: 不需要已训练 ModelVersion,
    ultralytics 会自动下载预训练权重 (yolov8n.pt 等).
    """
    from app.database import AsyncSessionLocal
    from app.models import Image as ImageModel
    from app.models.bbox_annotation import BBoxAnnotation
    from app.models.annotation_log import AnnotationLog
    from app.models.category import Category
    from app.ml.detection import predict_image_grouped, YoloTrainError
    from sqlalchemy import select, delete

    task_id = self.request.id

    if model_name not in PREDEFINED_YOLO_MODELS:
        return {
            "status": "FAILURE",
            "error": f"不支持的预训练模型: {model_name}, 可选 {PREDEFINED_YOLO_MODELS}",
        }

    try:
        # 1) 拉全部 detection 图
        async def _load_images() -> list:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(ImageModel)
                    .where(
                        ImageModel.dataset_id == dataset_id,
                        ImageModel.task_type == "detection",
                    )
                    .order_by(ImageModel.id.asc())
                )).scalars().all()
                # v2.5.30 修复: Image ORM 字段是 storage_path (与 auto_annotate_detection_task 同源)
                return [(r.id, r.storage_path) for r in rows]
        items = _run_async(_load_images())
        if not items:
            return {"status": "SUCCESS", "total": 0, "auto_labeled": 0, "no_match": 0}

        # 2) 拉 Category
        async def _load_cats() -> list:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(Category)
                    .where(Category.dataset_id == dataset_id)
                    .order_by(Category.id.asc())
                )).scalars().all()
                return list(rows)
        cats = _run_async(_load_cats())
        index_to_cat = {i: c for i, c in enumerate(cats)}

        # 3) 拼图片绝对路径 + 过滤存在
        from app.services.storage_service import storage_service
        abs_paths = []
        valid_ids = []
        for img_id, fp in items:
            p = Path(fp)
            if not p.is_absolute():
                p = (Path(storage_service.base_path).resolve() / fp).resolve()
            if p.exists():
                abs_paths.append(str(p))
                valid_ids.append(img_id)

        if not abs_paths:
            return {"status": "SUCCESS", "total": 0, "auto_labeled": 0, "no_match": 0}

        # 4) 加载 ultralytics 预训练权重
        # ultralytics 会自动下载 yolov8n.pt 到 ~/.cache/ultralytics/
        try:
            from ultralytics import YOLO
        except ImportError as e:
            return {"status": "FAILURE", "error": f"ultralytics 未安装: {e}"}
        try:
            pretrained = YOLO(f"{model_name}.pt")
            weights_path = str(pretrained.ckpt_path) if hasattr(pretrained, "ckpt_path") else f"{model_name}.pt"
        except Exception as e:
            return {"status": "FAILURE", "error": f"加载预训练权重失败: {e}"}

        # 5) 推理 (复用 predict_image_grouped, 与已训练模型走同一条路径)
        def _progress_cb(p, msg):
            _set_task_state(self, "PROGRESS", {
                "progress": round(p, 2),
                "msg": msg,
                "total": len(abs_paths),
            })

        grouped = predict_image_grouped(
            weights_path=weights_path,
            image_paths=abs_paths,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
            imgsz=imgsz,
            device=device,
        )

        # 6) 写 BBoxAnnotation
        async def _write_results():
            async with AsyncSessionLocal() as db:
                auto_labeled = 0
                no_match = 0
                for i, (img_id, p) in enumerate(zip(valid_ids, abs_paths)):
                    boxes = grouped.get(p, [])
                    if not boxes:
                        no_match += 1
                        continue
                    if overwrite_existing:
                        await db.execute(
                            delete(BBoxAnnotation).where(BBoxAnnotation.image_id == img_id)
                        )
                    for b in boxes:
                        cat = index_to_cat.get(b.get("class_index"))
                        if not cat:
                            continue
                        # v2.5.15 P0-1 修复: BBoxAnnotation ORM 不存在 model_name 字段
                        # 之前 model_name=model_name 会抛 AttributeError. 现改为:
                        # - 移除 ORM 不存在的字段
                        # - 把模型名写到 AnnotationLog.payload (审计可追溯)
                        row = BBoxAnnotation(
                            image_id=img_id,
                            category_id=cat.id,
                            x_min=b["x_min"], y_min=b["y_min"],
                            x_max=b["x_max"], y_max=b["y_max"],
                            confidence=float(b.get("confidence", 0.0)),
                            # v2.5.15 P0-1.1 修复: bbox_source 枚举仅 ai/human/human_corrected,
                            # 之前误写 "pretrained" 会抛 LookupError. 改为 "ai" (AI 自动标注语义一致)
                            source="ai",
                        )
                        db.add(row)
                    # v2.5.15 P0-2 修复: AnnotationLog.action 枚举已扩展
                    # 旧: action="auto_annotate_pretrained" 会抛 ValueError 越界
                    # 新: "auto_annotate_pretrained" 已在 annotation_log.py Enum 中注册
                    db.add(AnnotationLog(
                        image_id=img_id,
                        user_id=user_id,
                        action="auto_annotate_pretrained",
                        payload={"model": model_name, "n_boxes": len(boxes)},
                    ))
                    auto_labeled += 1
                await db.commit()
                return auto_labeled, no_match

        _set_task_state(self, "PROGRESS", {
            "progress": 0.0, "msg": f"写入 bbox (模型={model_name})...",
            "total": len(abs_paths),
        })
        auto_labeled, no_match = _run_async(_write_results())

        _set_task_state(self, "SUCCESS", {
            "progress": 1.0, "msg": "完成",
            "total": len(abs_paths),
            "auto_labeled": auto_labeled,
            "no_match": no_match,
        })
        return {
            "status": "SUCCESS",
            "model_name": model_name,
            "weights": weights_path,
            "total": len(abs_paths),
            "auto_labeled": auto_labeled,
            "no_match": no_match,
        }

    except Exception as e:
        _set_task_state(self, "FAILURE", {
            "exc_type": type(e).__name__,
            "exc_message": str(e)[:200],
            "error": str(e)[:500],
        })
        return {"status": "FAILURE", "error": str(e)[:500]}
