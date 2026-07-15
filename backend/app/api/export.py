"""
Export API: 标注导出
====================
支持 COCO / YOLO / CSV 三种主流标注格式
论文实验数据可基于此接口导出
"""
import csv
import json
import io
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.image import Image
from app.models.category import Category
from app.models.dataset import Dataset
from app.models.user import User
from app.core.deps import get_current_user

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
