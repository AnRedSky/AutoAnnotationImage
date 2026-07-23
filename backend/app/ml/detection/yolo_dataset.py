"""
YOLO Dataset Adapter (v2.0.0 目标检测)
========================================

职责:
- 读 DB 中的 BBoxAnnotation
- 写成 ultralytics YOLOv8 所需的 YOLO txt 格式
- 生成 data.yaml (类别索引 + 训练/校验集路径)

YOLO 训练目录布局:
  <workdir>/
    images/
      train/<image1>.jpg
      val/<image2>.jpg
    labels/
      train/<image1>.txt
      val/<image2>.txt
    data.yaml

约定:
- 坐标统一: 归一化 0-1 (与 BBoxAnnotation 存储一致)
- 类别索引: 0-based, 与 Category.id 不绑定 (按 datasets 列表顺序映射)
- 软链/拷贝: 默认 hardlink (节省空间, 同盘上瞬时), 跨盘自动 fallback 拷贝
- 进度: callback(stage: str, current: int, total: int, info: str = "")
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Image as ImageModel
from app.models import Category
from app.models.bbox_annotation import BBoxAnnotation
from app.services.bbox_service import bbox_to_yolo_line, validate_normalized_bbox
from app.services.storage_service import storage_service


ProgressCallback = Optional[Callable[[str, int, int, str], None]]


# ============== 类别索引映射 ==============

async def build_class_index_map(
    db: AsyncSession,
    dataset_id: int,
) -> List[Category]:
    """读出 dataset 全部 Category, 按 id 升序得到 [Category, ...]

    - 数组下标 = 训练时 class_index (0-based)
    - 与 Category.id 解耦: 类别可能被删/被新建, 训练时按当前列表为准
    - 缺类别时, YOLO 仍能跑 (单类检测, class_index 全部为 0)
    """
    rows = (await db.execute(
        select(Category)
        .where(Category.dataset_id == dataset_id)
        .order_by(Category.id.asc())
    )).scalars().all()
    return list(rows)


# ============== 工具: 图片源路径 ==============

def _resolve_image_path(image: ImageModel) -> Path:
    """ImageModel.storage_path → 绝对路径

    兼容 image.storage_path 是相对路径 (如 'ds_1/abc.png') 或绝对路径.
    相对路径: 拼 storage_service.base_dir (settings.UPLOAD_DIR) 的绝对路径
    文件不存在时 raise FileNotFoundError (上层捕获)
    """
    sp = image.storage_path
    p = Path(sp)
    if p.is_absolute():
        return p
    base = Path(storage_service.base_dir).resolve()
    return (base / sp).resolve()


# ============== 拆分 train/val ==============

def split_train_val(
    image_ids: Sequence[int],
    val_ratio: float = 0.2,
    seed: int = 42,
) -> Tuple[List[int], List[int]]:
    """按 image_ids 拆分 train / val, 随机但可复现 (固定 seed)

    Args:
        image_ids: 该 dataset 下全部 image.id
        val_ratio: 校验集占比 (0.0 ~ 1.0)
        seed: 随机种子

    Returns:
        (train_ids, val_ids)
    """
    import random
    rng = random.Random(seed)
    ids = list(image_ids)
    rng.shuffle(ids)
    n_val = max(1, int(len(ids) * val_ratio)) if len(ids) > 1 else 0
    val_ids = ids[:n_val]
    train_ids = ids[n_val:]
    return train_ids, val_ids


# ============== 单图导出: BBoxAnnotation 列表 → YOLO txt 行 ==============

def annotations_to_yolo_lines(
    annotations: Sequence[BBoxAnnotation],
    class_index_map: Dict[int, int],
) -> List[str]:
    """一组 BBoxAnnotation → YOLO txt 多行 (每框一行)

    Args:
        annotations: ORM 列表 (来自 select BBoxAnnotation)
        class_index_map: {category_id: class_index}, 无 category 的框丢弃

    Returns:
        过滤后 YOLO txt 行, 含坐标合法性校验
    """
    lines: List[str] = []
    for ann in annotations:
        if ann.category_id is None:
            continue  # 无类别不参与训练 (YOLO 必须有 class)
        cls = class_index_map.get(ann.category_id)
        if cls is None:
            continue
        try:
            validate_normalized_bbox(
                ann.x_min, ann.y_min, ann.x_max, ann.y_max,
            )
        except ValueError:
            # 非法标注: 跳过, 不阻断训练
            continue

        # 通过 bbox_service 转 YOLO 格式
        # 这里不构造 BBox dataclass, 直接拼行
        cx = (ann.x_min + ann.x_max) / 2.0
        cy = (ann.y_min + ann.y_max) / 2.0
        w = max(0.0, ann.x_max - ann.x_min)
        h = max(0.0, ann.y_max - ann.y_min)
        lines.append(f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return lines


# ============== 主入口: 写 YOLO 训练目录 ==============

async def export_yolo_dataset(
    db: AsyncSession,
    dataset_id: int,
    workdir: str | os.PathLike,
    val_ratio: float = 0.2,
    progress_cb: ProgressCallback = None,
) -> Dict:
    """
    把指定 dataset 的 BBoxAnnotation 导出为 YOLO 训练目录 + data.yaml

    Args:
        db: AsyncSession
        dataset_id: 数据集 id (必须是 detection 类型, 由调用方保证)
        workdir: 输出目录, 会被清空重建
        val_ratio: 校验集比例
        progress_cb: 进度回调

    Returns:
        dict {
            "workdir": str,
            "classes": List[str],     # 类别名, 与 class_index 一一对应
            "train_count": int,
            "val_count": int,
            "skipped_no_bbox": int,
            "data_yaml": str,         # 绝对路径
        }

    Raises:
        FileNotFoundError: 源图片找不到
        ValueError: dataset 不存在 / 无图片
    """
    workdir = Path(workdir).resolve()
    if workdir.exists():
        shutil.rmtree(workdir)
    (workdir / "images" / "train").mkdir(parents=True)
    (workdir / "images" / "val").mkdir(parents=True)
    (workdir / "labels" / "train").mkdir(parents=True)
    (workdir / "labels" / "val").mkdir(parents=True)

    # 1) 拉所有 Image (仅 detection)
    images = (await db.execute(
        select(ImageModel)
        .where(
            ImageModel.dataset_id == dataset_id,
            ImageModel.task_type == "detection",
        )
        .order_by(ImageModel.id.asc())
    )).scalars().all()
    if not images:
        raise ValueError(f"dataset id={dataset_id} 下无 detection 图片")

    # 2) 拉所有 BBoxAnnotation (一次 query, 内存 group by image_id)
    img_ids = [img.id for img in images]
    ann_rows = (await db.execute(
        select(BBoxAnnotation)
        .where(BBoxAnnotation.image_id.in_(img_ids))
    )).scalars().all()
    ann_by_img: Dict[int, List[BBoxAnnotation]] = {}
    for a in ann_rows:
        ann_by_img.setdefault(a.image_id, []).append(a)

    # 3) 类别索引
    cats = await build_class_index_map(db, dataset_id)
    class_index_map = {c.id: i for i, c in enumerate(cats)}
    class_names = [c.name for c in cats] or ["object"]

    # 4) 拆分 train/val
    train_ids, val_ids = split_train_val(img_ids, val_ratio=val_ratio)
    train_set, val_set = set(train_ids), set(val_ids)
    if progress_cb:
        progress_cb("export.start", 0, len(images), f"train={len(train_ids)} val={len(val_ids)}")

    # 5) 逐图写文件
    skipped = 0
    written_train = 0
    written_val = 0
    for i, img in enumerate(images, start=1):
        try:
            src = _resolve_image_path(img)
            if not src.exists():
                skipped += 1
                if progress_cb:
                    progress_cb("export.skip", i, len(images),
                                f"missing: {src}")
                continue
            # 该图有标注?  无标注也跳过 (YOLO 训练不需要无标注图)
            img_anns = ann_by_img.get(img.id, [])
            img_anns = [a for a in img_anns if a.category_id is not None]
            if not img_anns:
                skipped += 1
                if progress_cb:
                    progress_cb("export.skip", i, len(images),
                                f"no annotations: img_id={img.id}")
                continue
            split = "train" if img.id in train_set else "val"
            if split == "train":
                written_train += 1
            else:
                written_val += 1
            # 5.1) 链接/拷贝图片
            dst_img = workdir / "images" / split / src.name
            try:
                os.link(src, dst_img)  # hardlink, 同盘瞬时
            except OSError:
                shutil.copy2(src, dst_img)
            # 5.2) 写 YOLO txt
            lines = annotations_to_yolo_lines(img_anns, class_index_map)
            dst_lbl = workdir / "labels" / split / f"{src.stem}.txt"
            dst_lbl.write_text("\n".join(lines) + ("\n" if lines else ""),
                               encoding="utf-8")
        except Exception as e:  # noqa: BLE001 (单图失败不阻塞其他)
            skipped += 1
            if progress_cb:
                progress_cb("export.skip", i, len(images), f"err: {e}")
            continue
        if progress_cb and i % 5 == 0:
            progress_cb("export.image", i, len(images), src.name)

    # 6) 写 data.yaml
    data_yaml = workdir / "data.yaml"
    data_yaml.write_text(
        f"path: {workdir.as_posix()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"nc: {len(class_names)}\n"
        f"names: {class_names!r}\n",
        encoding="utf-8",
    )

    train_count = written_train
    val_count = written_val
    if progress_cb:
        progress_cb("export.done", len(images), len(images),
                    f"train={train_count} val={val_count} skipped={skipped}")

    return {
        "workdir": str(workdir),
        "classes": class_names,
        "train_count": train_count,
        "val_count": val_count,
        "skipped_no_bbox": skipped,
        "data_yaml": str(data_yaml),
    }
