"""
export.segmentation 模块 — 图像分割数据集导出
============================================

**v3.0.0 Phase S1 拆分**: 从 export.py 抽离
**职责**: 分割任务标注的 2 种格式导出 (VOC / COCO)

**路由清单** (2 个):
- GET /voc-seg/{dataset_id}   VOC 分割 zip (JPEGImages + SegmentationClass + ImageSets)
- GET /coco-seg/{dataset_id}  COCO 分割 JSON (polygon 包围外接矩形, MVP 简化 RLE)

**S1 分割导出约定**:
- 强制要求 dataset.task_type == "segmentation", 否则 400
- VOC-seg: 行业标准 PASCAL VOC 分割目录布局
- COCO-seg: COCO segmentation 字段 (用 polygon 包围外接矩形, MVP 够用)
- AI 预标注已在 /api/segmentation/auto-annotate 端点实现, 此处不重复

**S2 工具函数**:
- `_resolve_mask_path`: SegmentationMask.mask_path -> 绝对路径
- `_resolve_image_path_for_export`: Image.storage_path -> 绝对路径 (与 yolo_dataset 同源)
"""
import io
import json
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
from app.annotation.model.segmentation_mask import SegmentationMask
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.common.enums import TaskType

router = APIRouter()


# ============== 工具函数 ==============

def _resolve_mask_path(mask_path: str) -> Path:
    """SegmentationMask.mask_path -> 绝对路径"""
    p = Path(mask_path)
    if p.is_absolute():
        return p
    from app.common.storage.storage_service import storage_service
    return (Path(storage_service.base_dir) / mask_path).resolve()


def _resolve_image_path_for_export(image: Image) -> Path:
    """Image.storage_path -> 绝对路径 (与 yolo_dataset 同源)"""
    from app.common.storage.storage_service import storage_service
    sp = image.storage_path
    p = Path(sp)
    if p.is_absolute():
        return p
    base = Path(storage_service.base_dir).resolve()
    return (base / sp).resolve()


# ============== VOC 分割导出 ==============

@router.get("/voc-seg/{dataset_id}")
async def export_voc_segmentation(
    dataset_id: int,
    include_pending: bool = Query(False),
    val_ratio: float = Query(0.2, ge=0.0, lt=1.0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    导出 VOC 分割格式 zip:
      JPEGImages/<image>.jpg
      SegmentationClass/<image>.png   (索引图, 像素 = 类别 id)
      SegmentationObject/<image>.png  (实例分割: 简化 = 与 Class 同值)
      ImageSets/Segmentation/{train,val,trainval}.txt
      label_colors.txt                (R G B 类别名, 调色板)
    """
    from PIL import Image as PILImage

    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if dataset.task_type != TaskType.SEGMENTATION.value:
        raise HTTPException(
            400, f"Dataset task_type={dataset.task_type!r}, expected 'segmentation'",
        )

    # 1) 拉 segmentation 图 + mask
    imgs = (await db.execute(
        select(Image)
        .where(
            Image.dataset_id == dataset_id,
            Image.task_type == TaskType.SEGMENTATION.value,
            Image.quality_flag.is_(None),  # v3.0.0: 排除不合格图片
        )
        .order_by(Image.id.asc())
    )).scalars().all()

    img_ids = [im.id for im in imgs]
    mask_rows: list[SegmentationMask] = []
    if img_ids:
        mask_rows = (await db.execute(
            select(SegmentationMask).where(SegmentationMask.image_id.in_(img_ids))
        )).scalars().all()
    mask_by_img = {m.image_id: m for m in mask_rows}

    cats = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
        .order_by(Category.id.asc())
    )).scalars().all()

    # 2) train/val 拆分
    from app.tasks.ml.detection.yolo_dataset import split_train_val
    train_ids, val_ids = split_train_val(img_ids, val_ratio=val_ratio)
    train_set, val_set = set(train_ids), set(val_ids)

    # 3) 打包 zip
    buf = io.BytesIO()
    written_train: list[str] = []
    written_val: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for im in imgs:
            stem = (im.filename or f"image_{im.id}").rsplit(".", 1)[0]
            # 3.1) JPEGImages (从原图存, 简单 .jpg 命名)
            try:
                src_path = _resolve_image_path_for_export(im)
                with PILImage.open(src_path) as pil:
                    if pil.mode != "RGB":
                        pil = pil.convert("RGB")
                    jpeg_buf = io.BytesIO()
                    pil.save(jpeg_buf, format="JPEG", quality=90)
                    zf.writestr(f"JPEGImages/{stem}.jpg", jpeg_buf.getvalue())
            except Exception:
                continue
            # 3.2) SegmentationClass (mask PNG, 索引)
            m = mask_by_img.get(im.id)
            if m and not include_pending and im.status not in (
                "human_confirmed", "human_corrected", "trained",
            ):
                continue
            if m:
                try:
                    mask_abs = _resolve_mask_path(m.mask_path)
                    with open(mask_abs, "rb") as f:
                        zf.writestr(f"SegmentationClass/{stem}.png", f.read())
                    # 简化: SegmentationObject 与 Class 相同
                    with open(mask_abs, "rb") as f:
                        zf.writestr(f"SegmentationObject/{stem}.png", f.read())
                except Exception:
                    pass
            # 3.3) ImageSets
            if im.id in train_set:
                written_train.append(stem)
            else:
                written_val.append(stem)

        # ImageSets/Segmentation
        zf.writestr(
            "ImageSets/Segmentation/train.txt",
            "\n".join(written_train) + ("\n" if written_train else ""),
        )
        zf.writestr(
            "ImageSets/Segmentation/val.txt",
            "\n".join(written_val) + ("\n" if written_val else ""),
        )
        zf.writestr(
            "ImageSets/Segmentation/trainval.txt",
            "\n".join(written_train + written_val) + ("\n" if (written_train or written_val) else ""),
        )
        # label_colors.txt: R G B 类别名 (调色板)
        palette_lines = []
        for c in cats:
            # 简单 hash 颜色 (避免缺字段)
            h = abs(hash(c.name)) % (256 ** 3)
            r = (h >> 16) & 0xFF
            g = (h >> 8) & 0xFF
            b = h & 0xFF
            palette_lines.append(f"{r:3d} {g:3d} {b:3d} {c.name}")
        palette_text = (
            "# label_colors.txt  (R G B name) - 自动生成\n"
            "  0   0   0   background\n"
            + "\n".join(palette_lines)
            + "\n"
        )
        zf.writestr("label_colors.txt", palette_text)

    buf.seek(0)
    body = buf.getvalue()
    return StreamingResponse(
        iter([body]),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="dataset_{dataset_id}_voc_seg.zip"'
            ),
            "X-Train-Count": str(len(written_train)),
            "X-Val-Count": str(len(written_val)),
        },
    )


# ============== COCO 分割导出 ==============

@router.get("/coco-seg/{dataset_id}")
async def export_coco_segmentation(
    dataset_id: int,
    include_pending: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    导出 COCO 分割格式 JSON (简化 RLE / polygon 兼容):
      {
        info, images, categories,
        annotations: [{id, image_id, category_id,
                       segmentation: [[x1,y1,...]],  # polygon 包围外接矩形 (MVP 简化)
                       bbox: [x, y, w, h],
                       area, iscrowd}]
      }

    说明:
    - COCO segmentation 接受 RLE 或 polygon, 此处用 polygon (4 顶点的 bbox polygon)
    - 完整连通域 RLE 需要 pycocotools, 当前用 polygon 等价表达
    """
    import numpy as np
    from PIL import Image as PILImage

    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if dataset.task_type != TaskType.SEGMENTATION.value:
        raise HTTPException(
            400, f"Dataset task_type={dataset.task_type!r}, expected 'segmentation'",
        )

    # 1) 拉图 + mask
    imgs = (await db.execute(
        select(Image)
        .where(
            Image.dataset_id == dataset_id,
            Image.task_type == TaskType.SEGMENTATION.value,
            Image.quality_flag.is_(None),  # v3.0.0: 排除不合格图片
        )
        .order_by(Image.id.asc())
    )).scalars().all()
    img_ids = [im.id for im in imgs]
    mask_rows: list[SegmentationMask] = []
    if img_ids:
        mask_rows = (await db.execute(
            select(SegmentationMask).where(SegmentationMask.image_id.in_(img_ids))
        )).scalars().all()
    mask_by_img = {m.image_id: m for m in mask_rows}

    cats = (await db.execute(
        select(Category).where(Category.dataset_id == dataset_id)
        .order_by(Category.id.asc())
    )).scalars().all()
    valid_cat_ids = {c.id for c in cats}

    # 2) 拼 COCO
    coco = {
        "info": {
            "description": f"Dataset {dataset.name} exported as COCO segmentation",
            "version": "1.0",
            "year": 2026,
            "task_type": "segmentation",
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
        m = mask_by_img.get(im.id)
        if not m:
            continue
        if not include_pending and im.status not in (
            "human_confirmed", "human_corrected", "trained",
        ):
            continue
        # 3) 读 mask, 按 category_id 提取每类的 polygon
        try:
            mask_abs = _resolve_mask_path(m.mask_path)
            with PILImage.open(mask_abs) as mp:
                if mp.mode not in ("P", "L"):
                    mp = mp.convert("L")
                arr = np.array(mp)
        except Exception:
            continue

        w_px, h_px = arr.shape[1], arr.shape[0]
        # 对每个有效类别, 找其连通区域; 简化: 用外接矩形 polygon
        unique_vals = sorted(set(arr.flatten().tolist()))
        for v in unique_vals:
            if v == 0 or v not in valid_cat_ids:
                continue  # 0=背景, 不入库
            ys, xs = np.where(arr == v)
            if len(xs) == 0:
                continue
            x_min, x_max = int(xs.min()), int(xs.max())
            y_min, y_max = int(ys.min()), int(ys.max())
            # polygon: 4 顶点 (矩形包围盒)
            polygon = [
                x_min, y_min,
                x_max, y_min,
                x_max, y_max,
                x_min, y_max,
            ]
            coco["annotations"].append({
                "id": ann_id,
                "image_id": im.id,
                "category_id": int(v),
                "segmentation": [polygon],
                "bbox": [
                    float(x_min), float(y_min),
                    float(x_max - x_min), float(y_max - y_min),
                ],
                "area": float((x_max - x_min) * (y_max - y_min)),
                "iscrowd": 0,
            })
            ann_id += 1

    content = json.dumps(coco, ensure_ascii=False, indent=2)
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="dataset_{dataset_id}_coco_seg.json"'
            ),
            "X-Annotation-Count": str(len(coco["annotations"])),
            "X-Image-Count": str(len(coco["images"])),
        },
    )
