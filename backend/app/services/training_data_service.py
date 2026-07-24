"""
TrainingDataService — 训练数据加载与模型保存 (v3.0.0 Phase 5)
========================================================

**职责**:
- 训练数据加载 (DB → list of (path, label_idx) 样本)
- 训练指标入库 (ModelVersion 写入)
- 训练相关数据访问的解耦层

**v3.0.0 Phase 5 设计**:
- 从 `ml/train.py:run_training` 抽出所有 DB IO 逻辑
- ML 模块只接收"已加载样本"和"已计算指标", 不再 import app.models / app.database
- Worker 通过 `data_loader` / `model_saver` 回调注入本服务

**API 调用模式**:
```python
# 旧 (DB 耦合在 ML):
result = run_training(dataset_id=1, ...)

# 新 (ML 解耦, Worker 注入):
def _data_loader(dataset_id):
    return TrainingDataService.load_classification_samples(dataset_id)
def _model_saver(**kwargs):
    return TrainingDataService.save_classification_model_version(**kwargs)
result = run_training(
    dataset_id=1,
    data_loader=_data_loader,
    model_saver=_model_saver,
    ...
)
```
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TrainingDataService:
    """训练数据加载与模型保存 (无状态, 静态方法)"""

    # ============== 分类训练数据加载 ==============

    @staticmethod
    async def load_classification_samples(dataset_id: int) -> Dict[str, Any]:
        """加载分类训练样本 (从 DB 读已标注图片 + 类目)

        Returns:
            dict with keys:
            - samples: List[(abs_path, label_idx)]  已过滤孤儿/磁盘缺失
            - label_name_to_idx: Dict[str, int]
            - num_classes: int
            - skipped_orphan: int
            - skipped_missing: int
            - total_before_filter: int
            - backfilled_count: int  (ai_labeled 孤儿回填 final_label_id)
        """
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.model.image import Image
        from app.model.category import Category
        from app.config import settings

        async with AsyncSessionLocal() as db:
            # 1) 加载已确认标注图片 (含 ai_labeled 孤儿, LEFT JOIN 兼容)
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

            # 2) 回填 ai_labeled 孤儿 (final_label_id=NULL 但 ai_prediction.top1 在类目内)
            orphans_to_backfill: List[Tuple[int, int]] = []
            for img, cat in results:
                if cat is None and img.status == "ai_labeled" and img.ai_prediction:
                    top1 = (img.ai_prediction or {}).get("top1")
                    if top1 and top1 in categories.values():
                        cat_id = next(
                            cid for cid, cname in categories.items() if cname == top1
                        )
                        orphans_to_backfill.append((img.id, cat_id))
            if orphans_to_backfill:
                from app.model.image import Image as _Image
                for img_id, cat_id in orphans_to_backfill:
                    img_row = await db.get(_Image, img_id)
                    if img_row and img_row.final_label_id is None:
                        img_row.final_label_id = cat_id
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()

        # 3) 构建样本 (过滤孤儿 + 磁盘文件存在)
        label_name_to_idx = {name: i for i, name in enumerate(sorted(set(categories.values())))}
        samples: List[Tuple[str, int]] = []
        skipped_orphan = 0
        skipped_missing = 0
        for img, cat in results:
            if cat is None:
                skipped_orphan += 1
                continue
            full_path = settings.UPLOAD_DIR / img.storage_path
            if not full_path.exists():
                skipped_missing += 1
                continue
            samples.append((str(full_path), label_name_to_idx[cat.name]))

        return {
            "samples": samples,
            "label_name_to_idx": label_name_to_idx,
            "num_classes": len(label_name_to_idx),
            "skipped_orphan": skipped_orphan,
            "skipped_missing": skipped_missing,
            "total_before_filter": len(results),
            "backfilled_count": len(orphans_to_backfill),
        }

    @staticmethod
    def load_classification_samples_sync(dataset_id: int) -> Dict[str, Any]:
        """同步包装 (worker 调用)"""
        from app.core.celery_utils import run_async_in_worker as _run_async
        return _run_async(TrainingDataService.load_classification_samples(dataset_id))

    # ============== 分类 ModelVersion 写入 ==============

    @staticmethod
    async def save_classification_model_version(
        *,
        name: str,
        base_model: str,
        dataset_id: int,
        num_classes: int,
        file_path: str,
        accuracy: float,
        report: Dict[str, Any],
        history: Dict[str, Any],
        confusion_matrix: Any,
        device_info: Optional[Dict[str, Any]] = None,
    ) -> int:
        """写入分类 ModelVersion 记录 (含 evaluation metrics)

        Returns:
            ModelVersion.id
        """
        from app.database import AsyncSessionLocal
        from app.model.model_version import ModelVersion

        # 提取 macro avg 指标
        macro = report.get("macro avg", {}) if isinstance(report, dict) else {}

        # 转换 numpy → python 原生类型 (Celery 序列化要求)
        cm_json = confusion_matrix.tolist() if hasattr(confusion_matrix, "tolist") else (
            confusion_matrix if isinstance(confusion_matrix, list) else []
        )

        # 设备信息作为额外字段 (前端 Training.vue 详情页展示)
        extra_fields: Dict[str, Any] = {}
        if device_info:
            extra_fields["device_info"] = device_info
            if device_info.get("device_name"):
                extra_fields["device_name"] = str(device_info["device_name"])[:128]
            peak = device_info.get("gpu_peak_mb")
            if isinstance(peak, (int, float)):
                extra_fields["gpu_peak_memory_mb"] = int(peak)
            dv_type = device_info.get("device_type")
            if dv_type:
                extra_fields["device_type"] = str(dv_type)[:16]

        async with AsyncSessionLocal() as db:
            mv = ModelVersion(
                name=name,
                base_model=base_model,
                dataset_id=dataset_id,
                num_classes=num_classes,
                file_path=file_path,
                accuracy=accuracy,
                precision=macro.get("precision", 0),
                recall=macro.get("recall", 0),
                f1_score=macro.get("f1-score", 0),
                training_log=history,
                confusion_matrix=cm_json,
                is_active=False,
                **extra_fields,
            )
            db.add(mv)
            await db.commit()
            await db.refresh(mv)
            return mv.id

    @staticmethod
    def save_classification_model_version_sync(**kwargs) -> int:
        """同步包装 (worker 调用)"""
        from app.core.celery_utils import run_async_in_worker as _run_async
        return _run_async(TrainingDataService.save_classification_model_version(**kwargs))


__all__ = ["TrainingDataService"]
