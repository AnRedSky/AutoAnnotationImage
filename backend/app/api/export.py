"""
Export API: 标注导出
====================
支持 COCO / YOLO / CSV 三种主流标注格式 (含 v2.0.0 检测专用格式)
论文实验数据可基于此接口导出

v2.0.0 端点 (检测):
- GET /api/export/yolo-det/{ds_id}   YOLO 检测 zip (images + labels + data.yaml)
- GET /api/export/coco-det/{ds_id}   COCO 检测 JSON (含 bbox)
"""
import csv
import json
import io
import os
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.image import Image
from app.models.category import Category
from app.models.dataset import Dataset
from app.models.user import User
from app.models.bbox_annotation import BBoxAnnotation
from app.core.deps import get_current_user
from app.ml.detection.yolo_dataset import export_yolo_dataset
from app.schemas.enums import TaskType

router = APIRouter()


@router.get("/coco/{dataset_id}")
async def export_coco(
    dataset_id: int,
    include_pending: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    导出 COCO 格式标注（仅含已确认/修正的）
    COCO image classification 不原生支持，此处输出 "classification coco" 风格：
      - images: [{id, file_name, width, height}]
      - categories: [{id, name, supercategory}]
      - annotations: [{id, image_id, category_id}]
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # 取图片
    stmt = select(Image).where(Image.dataset_id == dataset_id)
    if not include_pending:
        stmt = stmt.where(Image.status.in_(["human_confirmed", "human_corrected", "trained"]))
    images = (await db.execute(stmt)).scalars().all()

    # 取类别
    cats = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()

    coco = {
        "info": {
            "description": f"Dataset {dataset.name} exported as COCO",
            "version": "1.0",
            "year": 2026,
        },
        "images": [
            {
                "id": img.id,
                "file_name": img.filename,
                "width": img.width,
                "height": img.height,
            }
            for img in images
        ],
        "categories": [
            {"id": c.id, "name": c.name, "supercategory": "default"}
            for c in cats
        ],
        "annotations": [
            {
                "id": idx + 1,
                "image_id": img.id,
                "category_id": img.final_label_id,
            }
            for idx, img in enumerate(images) if img.final_label_id
        ],
    }

    content = json.dumps(coco, ensure_ascii=False, indent=2)
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="dataset_{dataset_id}_coco.json"'},
    )


@router.get("/yolo/{dataset_id}")
async def export_yolo(
    dataset_id: int,
    include_pending: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    导出 YOLO 分类格式
    - classes.txt: 每行一个类别
    - labels/<image>.txt: 单行 "<class_idx> 0.5 0.5 1.0 1.0"
    打包为 zip 流式返回
    """
    import zipfile

    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    cats = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    cat_id_to_idx = {c.id: i for i, c in enumerate(cats)}

    stmt = select(Image).where(Image.dataset_id == dataset_id)
    if not include_pending:
        stmt = stmt.where(Image.status.in_(["human_confirmed", "human_corrected", "trained"]))
    images = (await db.execute(stmt)).scalars().all()

    # 打包 zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # classes.txt
        zf.writestr("classes.txt", "\n".join(c.name for c in cats))

        # labels/<image>.txt
        for img in images:
            if not img.final_label_id or img.final_label_id not in cat_id_to_idx:
                continue
            cls_idx = cat_id_to_idx[img.final_label_id]
            yolo_line = f"{cls_idx} 0.5 0.5 1.0 1.0\n"
            base = (img.filename or f"image_{img.id}").rsplit(".", 1)[0]
            zf.writestr(f"labels/{base}.txt", yolo_line)

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="dataset_{dataset_id}_yolo.zip"'},
    )


@router.get("/csv/{dataset_id}")
async def export_csv(
    dataset_id: int,
    include_pending: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    导出 CSV 明细表（用于论文统计 / Excel 分析）
    字段: image_id, filename, status, ai_top1, ai_top1_conf, final_label, annotated_at
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    stmt = select(Image).where(Image.dataset_id == dataset_id)
    if not include_pending:
        stmt = stmt.where(Image.status.in_(["human_confirmed", "human_corrected", "trained"]))
    images = (await db.execute(stmt)).scalars().all()

    cats = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    cat_map = {c.id: c.name for c in cats}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "image_id", "filename", "status",
        "ai_top1", "ai_top1_conf",
        "final_label", "annotated_at",
    ])
    for img in images:
        ai_top1 = ""
        ai_conf = ""
        if img.ai_prediction:
            ai_top1 = img.ai_prediction.get("top1", "")
            ai_conf = img.ai_prediction.get("top1_conf", "")
        writer.writerow([
            img.id,
            img.filename,
            img.status,
            ai_top1,
            ai_conf,
            cat_map.get(img.final_label_id, "") if img.final_label_id else "",
            img.annotated_at.isoformat() if img.annotated_at else "",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]),  # BOM 防止 Excel 乱码
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="dataset_{dataset_id}.csv"'},
    )


# ============== v2.0.0 目标检测专用导出 ==============
#
# 设计原则:
# - 与 classification 导出共用前缀 /api/export, 资源是同一个 dataset
# - 强制要求 dataset.task_type == "detection", 否则 400
# - YOLO-det: 复用 S3.1 export_yolo_dataset (写 workdir), 然后整目录打包 zip 流式返回
# - COCO-det: 内存拼 JSON, 一次性返回; bbox 用像素坐标 (COCO 标准)
#   - 缺 width/height 时, 用归一化坐标 × 1.0 作为兜底, area 同步计算


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

    # 1) 拉 detection images
    imgs = (await db.execute(
        select(Image)
        .where(
            Image.dataset_id == dataset_id,
            Image.task_type == TaskType.DETECTION.value,
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
