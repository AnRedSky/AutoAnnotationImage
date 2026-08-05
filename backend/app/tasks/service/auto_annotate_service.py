"""
AutoAnnotateService — AI 自动预标注业务编排
========================================

**职责**:
- 同步/异步预标注统一入口
- 决定 sync (小批量 < 50) / async (Celery) 模式
- 校验数据集 + 类目
- 调 ai_service.batch_predict + filter_predictions_to_categories
- 写 AnnotationLog (action=ai_predict)
- 联动 ImageService.mark_ai_labeled

**关键业务规则**:
- 仅 "pending" 状态的图被处理
- 置信度 < threshold: 保持 pending, 但 ai_prediction 仍存 (UI 可看候选)
- 置信度 >= threshold: 标 ai_labeled, 写审计
- 过滤到只含项目预设类目, 不在类目内 → no_match (不标, 保持 pending)

v3.0.0 Phase 4 补充
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.model.annotation_log import AnnotationLog
from app.tasks.model.category import Category
from app.tasks.model.dataset import Dataset
from app.tasks.model.image import Image
# v3.4.1 P1: 推理路径解析 (适配 minio 后端)
from app.common.storage import resolve_inference_paths
from app.common.ml.ai_service import ai_service, filter_predictions_to_categories
from app.tasks.service.image_service import ImageService

logger = logging.getLogger(__name__)


@dataclass
class AutoAnnotateResult:
    """预标注结果 (sync / async 模式共用返回结构)"""
    total: int
    auto_labeled: int
    need_human: int
    no_match: int = 0
    avg_confidence: float = 0.0
    threshold: float = 0.6
    task_id: Optional[str] = None  # async 模式有
    mode: str = "sync"  # "sync" | "async"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "auto_labeled": self.auto_labeled,
            "need_human": self.need_human,
            "no_match": self.no_match,
            "avg_confidence": self.avg_confidence,
            "threshold": self.threshold,
            "task_id": self.task_id,
            "mode": self.mode,
        }


# 触发异步模式的阈值 (>= 50 张)
ASYNC_THRESHOLD = 50


class AutoAnnotateService:
    """AI 预标注业务编排 (无状态, 静态方法)"""

    # ============== 校验 ==============

    @staticmethod
    async def assert_valid_request(
        db: AsyncSession,
        dataset_id: int,
    ) -> Dataset:
        """业务校验: 数据集存在 + 有类目"""
        dataset = await db.get(Dataset, dataset_id)
        if not dataset:
            raise NotFoundError("Dataset not found")
        # 检查类目
        cat_count = (await db.execute(
            select(Category).where(Category.dataset_id == dataset_id).limit(1)
        )).scalar_one_or_none()
        if not cat_count:
            raise ValidationError(
                "数据集没有预设类目, 请先添加类目 (Dataset -> 类别) 再启动 AI 预标注",
            )
        return dataset

    @staticmethod
    async def _load_categories(
        db: AsyncSession, dataset_id: int
    ) -> tuple[List[Category], Dict[str, int]]:
        """加载数据集全部类目 + 名称→id 映射"""
        cats = (await db.execute(
            select(Category).where(Category.dataset_id == dataset_id)
        )).scalars().all()
        name_to_id = {c.name: c.id for c in cats}
        return list(cats), name_to_id

    @staticmethod
    async def _load_pending_images(
        db: AsyncSession, dataset_id: int
    ) -> List[Image]:
        """加载待标注图 (status=pending)"""
        result = await db.execute(
            select(Image).where(
                Image.dataset_id == dataset_id,
                Image.status == "pending",
            )
        )
        return list(result.scalars().all())

    # ============== 同步模式 ==============

    @staticmethod
    async def run_sync(
        db: AsyncSession,
        *,
        dataset_id: int,
        model_name: str,
        confidence_threshold: float,
        user_id: int,
    ) -> AutoAnnotateResult:
        """同步预标注 (小批量 < ASYNC_THRESHOLD)

        流程:
        1. 校验数据集 + 类目
        2. 加载 model (若未加载)
        3. 批量推理
        4. 过滤到项目类目
        5. 写入 ai_prediction + 标 status
        6. 写 AnnotationLog
        """
        # 1) 校验
        await AutoAnnotateService.assert_valid_request(db, dataset_id)
        cat_rows, name_to_id = await AutoAnnotateService._load_categories(db, dataset_id)
        category_names = [c.name for c in cat_rows]
        images = await AutoAnnotateService._load_pending_images(db, dataset_id)
        total = len(images)
        if total == 0:
            return AutoAnnotateResult(
                total=0, auto_labeled=0, need_human=0, no_match=0,
                avg_confidence=0.0, threshold=confidence_threshold,
                mode="sync", task_id=None,
            )

        # 2) 加载 model
        if ai_service.current_model_name != model_name:
            try:
                await ai_service.load_pretrained(model_name)
            except Exception as e:
                err_msg = str(e)[:200]
                if any(k in err_msg.lower() for k in ["winerror 10060", "connection", "timeout", "huggingface"]):
                    raise HTTPException(
                        status_code=503,
                        detail=f"Model '{model_name}' cannot be loaded: no internet/HuggingFace access ({err_msg}).",
                    )
                raise HTTPException(500, f"Failed to load model: {err_msg}")
        # 3) 推理
        storage_root = settings.UPLOAD_DIR
        image_paths = [str(storage_root / img.storage_path) for img in images]
        predictions = await ai_service.batch_predict(image_paths, top_k=5)

        # 4) 过滤到项目类目
        filtered_preds = filter_predictions_to_categories(predictions, category_names)

        # 5) 写入
        auto_labeled = 0
        no_match = 0
        confs_for_avg: List[float] = []
        for img, fp in zip(images, filtered_preds):
            if fp is None:
                img.ai_prediction = None
                no_match += 1
                continue
            img.ai_prediction = fp
            confs_for_avg.append(fp["top1_conf"])
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
            # 否则: ai_prediction 存但 status 保持 pending (UI 展示候选)

        await db.commit()
        avg_conf = sum(confs_for_avg) / max(len(confs_for_avg), 1)

        return AutoAnnotateResult(
            total=total,
            auto_labeled=auto_labeled,
            need_human=total - auto_labeled,
            no_match=no_match,
            avg_confidence=round(avg_conf, 4),
            threshold=confidence_threshold,
            mode="sync",
            task_id=None,
        )

    # ============== 异步模式 ==============

    @staticmethod
    async def run_async(
        db: AsyncSession,
        *,
        dataset_id: int,
        model_name: str,
        confidence_threshold: float,
        user_id: int,
    ) -> AutoAnnotateResult:
        """异步预标注 (大批量 >= ASYNC_THRESHOLD, 走 Celery)"""
        await AutoAnnotateService.assert_valid_request(db, dataset_id)
        cat_rows, _ = await AutoAnnotateService._load_categories(db, dataset_id)
        category_names = [c.name for c in cat_rows]
        images = await AutoAnnotateService._load_pending_images(db, dataset_id)
        total = len(images)
        if total == 0:
            return AutoAnnotateResult(
                total=0, auto_labeled=0, need_human=0, no_match=0,
                avg_confidence=0.0, threshold=confidence_threshold,
                mode="async", task_id=None,
            )

        from app.tasks.workers.classification import auto_annotate_task
        async_result = auto_annotate_task.delay(
            dataset_id=dataset_id,
            model_name=model_name,
            confidence_threshold=confidence_threshold,
            user_id=user_id,
            category_names=category_names,
        )
        return AutoAnnotateResult(
            total=total,
            auto_labeled=0,
            need_human=total,
            no_match=0,
            avg_confidence=0.0,
            threshold=confidence_threshold,
            mode="async",
            task_id=async_result.id,
        )

    # ============== 统一入口 (按 size 自动选 sync/async) ==============

    @staticmethod
    async def run(
        db: AsyncSession,
        *,
        dataset_id: int,
        model_name: str,
        confidence_threshold: float,
        user_id: int,
        async_mode: bool = False,
    ) -> AutoAnnotateResult:
        """统一入口 (按 total >= ASYNC_THRESHOLD 或 async_mode=True 走异步)"""
        # 先快速判断数量
        images = await AutoAnnotateService._load_pending_images(db, dataset_id)
        total = len(images)
        use_async = async_mode or total >= ASYNC_THRESHOLD
        if use_async:
            return await AutoAnnotateService.run_async(
                db,
                dataset_id=dataset_id,
                model_name=model_name,
                confidence_threshold=confidence_threshold,
                user_id=user_id,
            )
        return await AutoAnnotateService.run_sync(
            db,
            dataset_id=dataset_id,
            model_name=model_name,
            confidence_threshold=confidence_threshold,
            user_id=user_id,
        )

    # ============== 状态查询 ==============

    @staticmethod
    async def get_async_status(
        task_id: str,
        current_user=None,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """查询异步任务状态 (供 SSE / 轮询用)

        v3.3.6-STATS-ISOLATION 修复 (P0-越权):
          - 之前: 任何登录用户可查询任意 task_id 的进度 (含他人数据集)
          - 现在: 校验 task meta 里的 user_id/dataset_id, 防止跨用户枚举
          - 严格最小权限: super_admin 也不旁路, 必须满足 owner / team_member

        Args:
            task_id: Celery 任务 id
            current_user: 当前登录用户 (供权限校验)
            db: AsyncSession (供 dataset 访问权校验)

        Returns:
            状态字典 {task_id, state, progress, message, total, auto_labeled}
        """
        from celery.result import AsyncResult
        state = "PENDING"
        info: Dict[str, Any] = {}
        try:
            result = AsyncResult(task_id)
            try:
                state = result.state or "PENDING"
            except Exception:
                state = "PENDING"
            try:
                raw_info = result.info
                if isinstance(raw_info, dict):
                    info = raw_info
            except Exception:
                info = {}
        except Exception:
            state = "PENDING"
            info = {}

        # v3.3.6-STATS-ISOLATION: 权限校验
        # 1) 任何登录用户必须满足: 自己创建的 task, 或有权访问关联 dataset
        # 2) 老的 task (没有 user_id meta) → 拒绝 (保守策略, 防止历史 task 越权)
        if current_user is not None and db is not None:
            task_user_id = info.get("user_id")
            task_dataset_id = info.get("dataset_id")
            # 老 task (无 user_id meta) → 拒绝访问, 提示重启任务
            if task_user_id is None:
                from fastapi import HTTPException
                raise HTTPException(
                    403,
                    "无权限访问此任务 (task meta 缺少 owner 标识, 旧版任务不可查询, 请重新启动)"
                )
            # 校验: 创建者本人 OR 关联 dataset 有权访问
            if int(task_user_id) == current_user.id:
                pass  # owner
            elif task_dataset_id is not None:
                # 走 dataset 权限校验 (含 team 共享)
                from app.tasks.model.dataset import Dataset as _Ds
                from app.tasks.service.permission_service import (
                    assert_can_access_dataset as _assert_ds,
                )
                ds = await db.get(_Ds, int(task_dataset_id))
                if ds is None:
                    from fastapi import HTTPException
                    raise HTTPException(404, "无权限: 关联数据集不存在")
                await _assert_ds(db, current_user, ds)
            else:
                from fastapi import HTTPException
                raise HTTPException(403, "无权限: 非任务创建者, 且无关联 dataset")

        return {
            "task_id": task_id,
            "state": state,
            "progress": info.get("progress", 0),
            "message": info.get("msg", ""),
            "total": info.get("total", 0),
            "auto_labeled": info.get("auto_labeled", 0),
        }


__all__ = ["AutoAnnotateService", "AutoAnnotateResult", "ASYNC_THRESHOLD"]
