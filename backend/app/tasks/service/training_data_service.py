"""
TrainingDataService — 训练数据加载与模型保存 (v3.0.0 Phase 5)
========================================================

**职责**:
- 训练数据加载 (DB → list of (path, label_idx) 样本)
- 训练指标入库 (ModelVersion 写入)
- 训练相关数据访问的解耦层

**v3.0.0 Phase 5 设计**:
- 从 `ml/train.py:run_training` 抽出所有 DB IO 逻辑
- ML 模块只接收"已加载样本"和"已计算指标", 不再 import ORM 模型层 / app.database
- Worker 通过 `data_loader` / `model_saver` 回调注入本服务

**v3.6.0 性能优化**:
- MinIO backend 下, 训练样本预下载从串行改为并发 (信号量限流), 240 图从 48s 降至 <5s
- 抽离 `_download_one_to_cache` 单元, 便于测试与复用
- 保留旧串行路径 (MINIO_DOWNLOAD_CONCURRENCY <= 1), 零回归

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

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TrainingDataService:
    """训练数据加载与模型保存 (无状态, 静态方法)"""

    # v3.0.0: 不合格类别纳入训练的最少样本数, 不足则自动降级 (跳过不合格类别)
    MIN_UNQUALIFIED_SAMPLES = 5
    # 虚拟类别名 (保留, 不应在 Category 表出现)
    # 与 app.common.enums.UNQUALIFIED_LABEL 同义, 这里复用字面量避免循环 import
    UNQUALIFIED_LABEL = "__unqualified__"

    # ============== v3.6.0: MinIO 并发下载辅助函数 ==============

    @staticmethod
    async def _download_one_to_cache(
        storage_path: str,
        local_path: "Path",
    ) -> bool:
        """下载单个对象到本地缓存路径 (v3.6.0 抽离, 便于并发调用 + 单元测试)

        Args:
            storage_path: MinIO 对象 key (storage_service 内部识别)
            local_path: 本地缓存文件路径 (调用方负责 mkdir parent)

        Returns:
            True=本地已有或下载成功; False=下载失败 (计入 skipped_missing)

        Notes:
            - 缓存命中 (local_path.exists()) 直接返回 True, 不重复下载
            - 任意异常被吞掉, 由调用方计入 skipped_missing (与旧行为一致)
            - 使用 aiofiles 异步写文件, 不阻塞 event loop
        """
        from app.common.storage.storage_service import storage_service
        import aiofiles

        if local_path.exists():
            return True
        try:
            content = await storage_service.load(storage_path)
            async with aiofiles.open(local_path, "wb") as f:
                await f.write(content)
            return True
        except Exception as e:
            # v3.6.0: 记录单条失败原因, 便于运维定位 MinIO 限流/网络问题
            logger.warning(
                f"[training_data_service] MinIO 预下载失败 (计入 skipped_missing): "
                f"storage_path={storage_path!r} local_path={local_path!r} error={e!r}"
            )
            return False

    @staticmethod
    async def _download_batch_concurrent(
        tasks: List[Tuple[str, "Path"]],
        concurrency: int,
    ) -> List[bool]:
        """并发执行一批 MinIO 下载任务 (v3.6.0 信号量限流)

        Args:
            tasks: List[(storage_path, local_path)]
            concurrency: 最大并发数, <=1 时退化为串行 (兼容旧行为)

        Returns:
            与 tasks 等长的 List[bool], True=成功或已存在, False=失败

        Notes:
            - 使用 asyncio.Semaphore 限流, 避免一次性打爆 MinIO
            - 任意单条失败不影响其他任务, return_exceptions=True
            - concurrency=1 时仍走 gather 但因为只放 1 个, 等价于串行
              (保留旧 if 路径仅为可读性, 实际两路径语义一致)
        """
        if concurrency <= 1 or len(tasks) <= 1:
            # 串行路径: 与旧 for 循环完全一致, 零回归
            return [await TrainingDataService._download_one_to_cache(sp, lp) for sp, lp in tasks]

        sem = asyncio.Semaphore(concurrency)

        async def _guarded(storage_path: str, local_path: "Path") -> bool:
            async with sem:
                return await TrainingDataService._download_one_to_cache(storage_path, local_path)

        results = await asyncio.gather(
            *[_guarded(sp, lp) for sp, lp in tasks],
            return_exceptions=False,  # _download_one_to_cache 内部已吞异常, 这里只可能 bool
        )
        return list(results)

    # ============== 分类训练数据加载 ==============

    @staticmethod
    async def load_classification_samples(
        dataset_id: int,
        include_unqualified: bool = True,
    ) -> Dict[str, Any]:
        """加载分类训练样本 (从 DB 读已标注图片 + 类目)

        Args:
            include_unqualified: True 时尝试将不合格图片作为虚拟类别 __unqualified__ 纳入训练;
                不合格样本 < MIN_UNQUALIFIED_SAMPLES 时自动降级 (跳过, unqualified_skipped=True)

        Returns:
            dict with keys:
            - samples: List[(abs_path, label_idx)]  已过滤孤儿/磁盘缺失
            - label_name_to_idx: Dict[str, int]
            - num_classes: int
            - class_names: List[str]  (按 label_idx 顺序, 供推理用)
            - skipped_orphan: int
            - skipped_missing: int
            - total_before_filter: int
            - backfilled_count: int  (ai_labeled 孤儿回填 final_label_id)
            - unqualified_count: int  (纳入训练的不合格样本数, 降级时为 0)
            - unqualified_skipped: bool  (True=因样本不足降级)
        """
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.tasks.model.image import Image
        from app.tasks.model.category import Category
        from app.core.config import settings

        async with AsyncSessionLocal() as db:
            # 1) 加载正常已确认标注图片 (quality_flag IS NULL)
            stmt = (
                select(Image, Category)
                .outerjoin(Category, Image.final_label_id == Category.id)
                .where(
                    Image.dataset_id == dataset_id,
                    Image.status.in_(["human_confirmed", "human_corrected", "ai_labeled"]),
                    Image.quality_flag.is_(None),
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
            # v3.1.0 Phase W3.4: 直接在已加载的 img ORM 对象上设置 final_label_id,
            # 不再逐条 db.get (N+1 查询). img 已在 results 中且已 attached 到 session.
            orphans_to_backfill: List[Tuple[int, int]] = []
            for img, cat in results:
                if cat is None and img.status == "ai_labeled" and img.ai_prediction:
                    top1 = (img.ai_prediction or {}).get("top1")
                    if top1 and top1 in categories.values():
                        cat_id = next(
                            cid for cid, cname in categories.items() if cname == top1
                        )
                        # 直接在已加载的 img 对象上回填, 无需重新 db.get
                        if img.final_label_id is None:
                            img.final_label_id = cat_id
                        orphans_to_backfill.append((img.id, cat_id))
            if orphans_to_backfill:
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()

            # 3) v3.0.0: 加载不合格图片 (quality_flag="unqualified") 作为虚拟类别
            unqualified_imgs: list = []
            if include_unqualified:
                uq_stmt = (
                    select(Image)
                    .where(
                        Image.dataset_id == dataset_id,
                        Image.quality_flag == "unqualified",
                    )
                )
                unqualified_imgs = list((await db.execute(uq_stmt)).scalars().all())

        # 4) 构建样本 (过滤孤儿 + 磁盘文件存在)
        # v3.3.0 修复: STORAGE_BACKEND=minio 时, 文件在 MinIO 而非本地磁盘.
        # 统一通过 storage_service 探测文件存在性, 保证 local/minio 后端都正确工作.
        # 注意: storage_service 是 _LazyStorageProxy, isinstance 检测无效, 直接用 settings.STORAGE_BACKEND 判断.
        from app.common.storage.storage_service import storage_service
        from app.utils.file_utils import safe_filename

        is_minio_backend = (settings.STORAGE_BACKEND or "local").lower() == "minio"

        label_name_to_idx = {name: i for i, name in enumerate(sorted(set(categories.values())))}
        samples: List[Tuple[str, int]] = []
        skipped_orphan = 0
        skipped_missing = 0
        # minio backend: 训练样本预下载到 worker 本地缓存目录
        local_cache_dir = settings.UPLOAD_DIR / "_training_cache" / str(dataset_id)
        if is_minio_backend:
            local_cache_dir.mkdir(parents=True, exist_ok=True)

        # v3.6.0: 收集所有待下载任务, 一次性并发 (信号量限流) 下载
        # 旧版逐条 await 串行下载, 240 张图需 48s; 并发 10 后 <5s.
        # 不合格图片走同一并发池 (后续步骤 5 共享 sem 限流配额).
        # 每个条目: (img, cat, local_path, label_idx)  仅 local 不存在的项参与下载
        download_plan: List[Tuple[Any, int, "Path", int]] = []  # (img, cat, local_path, label_idx)
        local_only_plan: List[Tuple[Any, int, str, int]] = []  # (img, cat, full_path, label_idx)

        for img, cat in results:
            if cat is None:
                skipped_orphan += 1
                continue
            # 通过 storage_service 检查文件存在 (local 走 os.path.exists, minio 走 SDK stat)
            try:
                if not storage_service.exists(img.storage_path):
                    skipped_missing += 1
                    continue
            except Exception:
                # MinIO 连接失败等异常 → 视为文件缺失, 计入 skipped_missing
                skipped_missing += 1
                continue

            if is_minio_backend:
                # 预下载到本地缓存 (key 转成安全文件名, 避免特殊字符)
                safe_name = safe_filename(img.storage_path.replace("/", "_"))
                local_path = local_cache_dir / safe_name
                download_plan.append((img, cat, local_path, label_name_to_idx[cat.name]))
            else:
                # local backend: 直接用 UPLOAD_DIR + storage_path
                full_path = settings.UPLOAD_DIR / img.storage_path
                local_only_plan.append((img, cat, str(full_path), label_name_to_idx[cat.name]))

        # 4.1) local backend 直接组装 samples (零 IO)
        for _img, _cat, _full, _lbl in local_only_plan:
            samples.append((_full, _lbl))

        # 4.2) minio backend: 先把已缓存的 local_path 直接用, 剩下的并发下载
        # v3.6.0: 并发信号量限流 (默认 10, 配置化 MINIO_DOWNLOAD_CONCURRENCY)
        concurrency = max(1, int(getattr(settings, "MINIO_DOWNLOAD_CONCURRENCY", 10) or 1))
        cache_hit_plan: List[Tuple[str, int]] = []  # (local_path, label_idx) 已存在
        download_tasks: List[Tuple[str, "Path"]] = []  # 待下载 (storage_path, local_path)
        download_label_idx: List[int] = []  # 与 download_tasks 一一对应的 label_idx

        for _img, _cat, local_path, lbl in download_plan:
            if local_path.exists():
                cache_hit_plan.append((str(local_path), lbl))
            else:
                download_tasks.append((_img.storage_path, local_path))
                download_label_idx.append(lbl)

        if download_tasks:
            results_ok = await TrainingDataService._download_batch_concurrent(
                download_tasks, concurrency=concurrency,
            )
            for ok, (storage_path, local_path), lbl in zip(
                results_ok, download_tasks, download_label_idx
            ):
                if ok:
                    samples.append((str(local_path), lbl))
                else:
                    skipped_missing += 1
        # 缓存命中直接 append
        for lp, lbl in cache_hit_plan:
            samples.append((lp, lbl))

        # 5) v3.0.0: 决策是否纳入不合格虚拟类别
        unqualified_count = 0
        unqualified_skipped = False
        if include_unqualified and len(unqualified_imgs) >= TrainingDataService.MIN_UNQUALIFIED_SAMPLES:
            # 纳入: 末尾追加 __unqualified__ 索引
            uq_idx = len(label_name_to_idx)
            label_name_to_idx[TrainingDataService.UNQUALIFIED_LABEL] = uq_idx

            # v3.6.0: 不合格图片也走并发预下载 (复用 4.2 的信号量逻辑)
            uq_cache_hit: List[Tuple[str, int]] = []
            uq_download_tasks: List[Tuple[str, "Path"]] = []
            uq_download_label_idx: List[int] = []
            uq_local_only: List[Tuple[str, int]] = []

            for img in unqualified_imgs:
                # v3.3.0 修复: 通过 storage_service 检测存在性 (兼容 minio backend)
                try:
                    if not storage_service.exists(img.storage_path):
                        skipped_missing += 1
                        continue
                except Exception:
                    skipped_missing += 1
                    continue
                if is_minio_backend:
                    # 预下载到本地缓存
                    safe_name = safe_filename(img.storage_path.replace("/", "_"))
                    local_path = local_cache_dir / safe_name
                    if local_path.exists():
                        uq_cache_hit.append((str(local_path), uq_idx))
                    else:
                        uq_download_tasks.append((img.storage_path, local_path))
                        uq_download_label_idx.append(uq_idx)
                else:
                    full_path = settings.UPLOAD_DIR / img.storage_path
                    uq_local_only.append((str(full_path), uq_idx))
                unqualified_count += 1

            # local 直接 append
            for full, lbl in uq_local_only:
                samples.append((full, lbl))
            # 缓存命中直接 append
            for lp, lbl in uq_cache_hit:
                samples.append((lp, lbl))
            # 并发下载不合格图片
            if uq_download_tasks:
                results_ok = await TrainingDataService._download_batch_concurrent(
                    uq_download_tasks, concurrency=concurrency,
                )
                for ok, (storage_path, local_path), lbl in zip(
                    results_ok, uq_download_tasks, uq_download_label_idx
                ):
                    if ok:
                        samples.append((str(local_path), lbl))
                    else:
                        skipped_missing += 1
        elif include_unqualified and len(unqualified_imgs) < TrainingDataService.MIN_UNQUALIFIED_SAMPLES:
            # 降级: 样本不足, 跳过不合格类别
            unqualified_skipped = True

        return {
            "samples": samples,
            "label_name_to_idx": label_name_to_idx,
            "num_classes": len(label_name_to_idx),
            "class_names": list(label_name_to_idx.keys()),
            "skipped_orphan": skipped_orphan,
            "skipped_missing": skipped_missing,
            "total_before_filter": len(results),
            "backfilled_count": len(orphans_to_backfill),
            "unqualified_count": unqualified_count,
            "unqualified_skipped": unqualified_skipped,
        }

    @staticmethod
    def load_classification_samples_sync(dataset_id: int) -> Dict[str, Any]:
        """同步包装 (worker 调用)"""
        from app.utils.async_helpers import run_async_in_worker as _run_async
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
        device_info: Optional[Dict[str, Any]] = None,  # noqa: ARG004 保留签名兼容 ml/classification.py
        class_names: Optional[List[str]] = None,
    ) -> int:
        """写入分类 ModelVersion 记录 (含 evaluation metrics)

        设备信息 (device_type/device_name/device_info/gpu_peak_memory_mb) 由调用方
        持久化到 TrainingJob (前端 TrainingDetailDialog.vue 直接读 job.device_*),
        ModelVersion 表无这些列, 这里不重复写入, 仅保留入参签名兼容 ml/classification.py.

        Args:
            class_names: v3.0.0 训练时的类别名称列表 (按 label_idx 顺序, 可能含虚拟
                __unqualified__). 推理时按此重建索引→类别名映射, 命中 __unqualified__
                索引 → 自动标记图片为不合格. None 时表示未启用不合格类别训练
                (旧模型 / 样本不足降级), 推理时 fallback 到 Category 表 sorted.

        Returns:
            ModelVersion.id
        """
        from app.database import AsyncSessionLocal
        from app.tasks.model.model_version import ModelVersion

        # 提取 macro avg 指标
        macro = report.get("macro avg", {}) if isinstance(report, dict) else {}

        # 转换 numpy → python 原生类型 (Celery 序列化要求)
        cm_json = confusion_matrix.tolist() if hasattr(confusion_matrix, "tolist") else (
            confusion_matrix if isinstance(confusion_matrix, list) else []
        )

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
                # v3.0.0: 类别名称列表 (含虚拟 __unqualified__), 供推理时重建索引→类别名映射
                class_names=class_names,
            )
            db.add(mv)
            await db.commit()
            await db.refresh(mv)
            return mv.id

    @staticmethod
    def save_classification_model_version_sync(**kwargs) -> int:
        """同步包装 (worker 调用)"""
        from app.utils.async_helpers import run_async_in_worker as _run_async
        return _run_async(TrainingDataService.save_classification_model_version(**kwargs))


__all__ = ["TrainingDataService"]
