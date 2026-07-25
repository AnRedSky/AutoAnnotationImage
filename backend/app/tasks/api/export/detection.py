"""
export.detection 模块 — 目标检测数据集导出
==========================================

**v3.0.0 Phase S1 拆分**: 从 export.py 抽离
**职责**: 检测任务标注的 2 种格式导出 (YOLO / COCO)

**路由清单** (2 个):
- GET /yolo-det/{dataset_id}   YOLO 检测 zip (images + labels + data.yaml)
- GET /coco-det/{dataset_id}   COCO 检测 JSON (含 bbox, 像素坐标)

**S1 检测导出约定**:
- 强制要求 dataset.task_type == "detection", 否则 400
- YOLO-det: 复用 S3.1 export_yolo_dataset 写 workdir, 然后整目录打包 zip
- COCO-det: 内存拼 JSON, 一次性返回; bbox 用像素坐标 (COCO 标准)
  - 缺 width/height 时, 用归一化坐标 × 1.0 作为兜底, area 同步计算
"""
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.category import Category
from app.tasks.model.dataset import Dataset
from app.annotation.model.bbox_annotation import BBoxAnnotation
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.ml.detection.yolo_dataset import export_yolo_dataset
from app.common.enums import TaskType

router = APIRouter()


@router.get("/yolo-det/{dataset_id}")
async def export_yolo_detection(
    dataset_id: int,
    val_ratio: float = Query(0.2, ge=0.0, lt=1.0, description="校验集比例"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    导出 YOLO 检测格式 zip:
      images/train/  images/val/
      labels/train/  labels/val/   (YOLO txt: class cx cy w h 归一化)
      data.yaml
    复用 S3.1 export_yolo_dataset 写 workdir, 然后打包 zip.
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if dataset.task_type != TaskType.DETECTION.value:
        raise HTTPException(
            400,
            f"Dataset task_type={dataset.task_type!r}, expected 'detection'",
        )

    # 1) export_yolo_dataset 写到临时目录 (含 images + labels + data.yaml)
    tmp_root = Path(tempfile.mkdtemp(prefix="yolo_det_export_"))
    try:
        info = await export_yolo_dataset(
            db=db, dataset_id=dataset_id,
            workdir=tmp_root, val_ratio=val_ratio, progress_cb=None,
        )
        workdir = Path(info["workdir"])

        # 2) 整目录打包 zip 流式返回
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(workdir):
                for name in files:
                    src = Path(root) / name
                    # zip 内路径相对 workdir (去除绝对前缀)
                    arc = src.relative_to(workdir).as_posix()
                    zf.write(src, arc)
        buf.seek(0)
        body = buf.getvalue()
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    return StreamingResponse(
        iter([body]),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="dataset_{dataset_id}_yolo_det.zip"'
            ),
            "X-Train-Count": str(info["train_count"]),
            "X-Val-Count": str(info["val_count"]),
            "X-Classes": ",".join(info["classes"]),
        },
    )


@router.get("/coco-det/{dataset_id}")
async def export_coco_detection(
    dataset_id: int,
    include_pending: bool = Query(False, description="是否包含未确认 bbox"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    导出 COCO 检测格式 JSON:
      {
        info, images, categories,
        annotations: [{id, image_id, category_id,
                       bbox: [x, y, w, h] (像素),
                       area, iscrowd}]
      }
    bbox 像素坐标 = 归一化 × image.width/height, 缺尺寸时退化为归一化值 (area 同步).
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if dataset.task_type != TaskType.DETECTION.value:
        raise HTTPException(
            400,
            f"Dataset task_type={dataset.task_type!r}, expected 'detection'",
        )

    # 1) 拉 detection images (v3.0.0: 排除不合格图片)
    imgs = (await db.execute(
        select(Image)
        .where(
            Image.dataset_id == dataset_id,
            Image.task_type == TaskType.DETECTION.value,
            Image.quality_flag.is_(None),  # v3.0.0: 排除不合格图片
        )
        .order_by(Image.id.asc())
    )).scalars().all()

    # 2) 拉全部 bbox, 内存 group by image_id
    img_ids = [im.id for im in imgs]
    bbox_rows: list[BBoxAnnotation] = []
    if img_ids:
        bbox_rows = (await db.execute(
            select(BBoxAnnotation).where(BBoxAnnotation.image_id.in_(img_ids))
        )).scalars().all()
    bbox_by_img: dict[int, list[BBoxAnnotation]] = {}
    for b in bbox_rows:
        bbox_by_img.setdefault(b.image_id, []).append(b)

    # 3) 类别 (dataset 全部 category, 不限 task_type)
    cats = (await db.execute(
        select(Category)
        .where(Category.dataset_id == dataset_id)
        .order_by(Category.id.asc())
    )).scalars().all()

    # 4) 拼 COCO
    coco = {
        "info": {
            "description": f"Dataset {dataset.name} exported as COCO detection",
            "version": "1.0",
            "year": 2026,
            "task_type": "detection",
        },
        "images": [
            {
                "id": im.id,
                "file_name": im.filename,
                "width": im.width or 0,
                "height": im.height or 0,
            }
            for im in imgs
        ],
        "categories": [
            {"id": c.id, "name": c.name, "supercategory": "default"}
            for c in cats
        ],
        "annotations": [],
    }

    ann_id = 1
    for im in imgs:
        w_px = im.width
        h_px = im.height
        for bb in bbox_by_img.get(im.id, []):
            if bb.category_id is None:
                continue  # 无类别不导出 (COCO 必须有 category_id)
            if not include_pending:
                # 仅保留已确认/已修正的 (与 classification 行为一致)
                if im.status not in (
                    "human_confirmed", "human_corrected", "trained",
                ):
                    continue
            # 归一化 → 像素
            if w_px and h_px:
                x = bb.x_min * w_px
                y = bb.y_min * h_px
                bw = (bb.x_max - bb.x_min) * w_px
                bh = (bb.y_max - bb.y_min) * h_px
            else:
                # 缺尺寸, 退化为归一化值
                x, y = bb.x_min, bb.y_min
                bw = max(0.0, bb.x_max - bb.x_min)
                bh = max(0.0, bb.y_max - bb.y_min)
            area = max(0.0, bw) * max(0.0, bh)
            coco["annotations"].append({
                "id": ann_id,
                "image_id": im.id,
                "category_id": bb.category_id,
                "bbox": [round(x, 2), round(y, 2), round(bw, 2), round(bh, 2)],
                "area": round(area, 2),
                "iscrowd": 0,
            })
            ann_id += 1

    content = json.dumps(coco, ensure_ascii=False, indent=2)
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="dataset_{dataset_id}_coco_det.json"'
            ),
            "X-Annotation-Count": str(len(coco["annotations"])),
            "X-Image-Count": str(len(coco["images"])),
        },
    )
