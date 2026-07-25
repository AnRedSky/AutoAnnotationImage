"""
export.classification 模块 — 分类数据集导出
============================================

**v3.0.0 Phase S1 拆分**: 从 export.py 抽离
**职责**: 分类任务标注的 3 种格式导出 (COCO / YOLO / CSV)

**路由清单** (3 个):
- GET /coco/{dataset_id}     COCO 分类 JSON (images + categories + annotations)
- GET /yolo/{dataset_id}     YOLO 分类 zip (classes.txt + labels/<image>.txt)
- GET /csv/{dataset_id}      CSV 明细表 (image_id, filename, status, ai_top1, final_label, annotated_at)

**S1 分类导出约定**:
- 仅基于 `Image.final_label_id` (单标签分类), 不涉及 bbox / mask
- 默认只导出已确认/已修正的图片 (status ∈ {human_confirmed, human_corrected, trained})
- `include_pending=True` 时包含待确认图片, 用于调试
- 类别按 Category.id 升序映射到 YOLO class index
"""
import csv
import io
import json
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.category import Category
from app.tasks.model.dataset import Dataset
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user

router = APIRouter()


# ============== 工具: 已确认图片过滤 ==============

_CONFIRMED_STATUSES = ("human_confirmed", "human_corrected", "trained")


def _filter_images_by_status(
    stmt, include_pending: bool
):
    """根据 include_pending 应用 status 过滤 (始终排除不合格图片)

    v3.0.0: 不合格图片 (quality_flag='unqualified') 不参与导出,
    无论 include_pending 与否都排除, 避免脏数据进入导出包.
    """
    stmt = stmt.where(Image.quality_flag.is_(None))
    if not include_pending:
        stmt = stmt.where(Image.status.in_(_CONFIRMED_STATUSES))
    return stmt


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
    stmt = _filter_images_by_status(
        select(Image).where(Image.dataset_id == dataset_id),
        include_pending,
    )
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
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    cats = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
    )).scalars().all()
    cat_id_to_idx = {c.id: i for i, c in enumerate(cats)}

    stmt = _filter_images_by_status(
        select(Image).where(Image.dataset_id == dataset_id),
        include_pending,
    )
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
    导出 CSV 明细表（用于业务统计 / Excel 分析）
    字段: image_id, filename, status, ai_top1, ai_top1_conf, final_label, annotated_at
    """
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    stmt = _filter_images_by_status(
        select(Image).where(Image.dataset_id == dataset_id),
        include_pending,
    )
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
