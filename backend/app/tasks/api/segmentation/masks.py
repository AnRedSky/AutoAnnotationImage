"""
segmentation.masks 模块 — Mask 标注 CRUD
========================================

**v3.0.0 Phase S2 拆分**: 从 segmentation.py 抽离
**职责**: Mask 标注的上传/拉取/删除/替换 + mask PNG 解析

**路由清单** (4 个):
- POST   /masks/upload/{image_id}   上传 / 替换单图 mask
- GET    /masks/{image_id}          拉取单图 mask (JSON 元数据 或 PNG 二进制)
- DELETE /masks/{mask_id}           删除 mask
- POST   /masks/replace             单图 mask 全量替换 (upload 的语义化别名)

**S2 mask 约定**:
- 物理存储: PNG 索引图 (P-mode, L-mode 也可), 像素值 = 类别索引
- 0 = 背景 (未标注); 像素值 N = Category.id = N 的类别 (按 Category.id 升序映射)
- mask_path 相对 UPLOAD_DIR, 写入 storage_service
- 仅 segmentation 数据集允许操作
- 一张图唯一一条 mask (UNIQUE image_id)
- v2.5.0-s12.8: 兼容前端 canvas.toBlob 的 RGBA PNG (取 R 通道当 L-mode)
"""
import io
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.image import Image
from app.annotation.model.segmentation_mask import SegmentationMask
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.common.enums import TaskType, AnnotationSource
from app.common.storage.storage_service import storage_service
from app.tasks.service.segmentation_service import SegmentationService
from PIL import Image as PILImage

router = APIRouter()


# ============== 工具函数 ==============

async def _ensure_segmentation_image(image_id: int, db: AsyncSession) -> Image:
    """校验图片存在且为 segmentation 任务类型"""
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, f"Image id={image_id} not found")
    if img.task_type != TaskType.SEGMENTATION.value:
        raise HTTPException(
            400,
            f"Image id={image_id} task_type is {img.task_type!r}, "
            f"expected 'segmentation'",
        )
    return img


def _read_mask_png(content: bytes) -> tuple[int, int, dict[int, int]]:
    """
    解析 PNG 索引图, 返回 (width, height, {pixel_value: count})

    - 仅接受 P-mode 或 L-mode
    - 自动转 P-mode (如果原图是 L, 等价)
    - 统计每种像素值的数量 (用于 category_pixel_counts)
    """
    try:
        pil = PILImage.open(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(400, f"无法解析 PNG: {e}")

    if pil.mode == "P":
        # P-mode: 索引值就是类别索引
        arr = pil
    elif pil.mode == "L":
        # L-mode: 灰度值直接当类别索引
        arr = pil
    elif pil.mode in ("RGB", "RGBA"):
        # v2.5.0-s12.8: 前端 canvas.toBlob 默认输出 RGBA PNG (color type 6),
        # 实际数据是 R=G=B=category_id, A=255. 取 R 通道当 L-mode 保持下游 arr.getdata() 兼容.
        # 业务上: mask 是单通道索引图, 调色板只是显示层.
        arr = pil.getchannel("R")  # PIL Image (L-mode), 灰度值 = R = category_id
    elif pil.mode == "1":
        # 二值图, 255/0 当类别
        arr = pil.convert("L")
    else:
        raise HTTPException(
            400, f"mask PNG 必须是 P-mode (索引) / L-mode (灰度) / RGB / RGBA, 当前 {pil.mode!r}",
        )

    # 统计像素值分布
    pixels = list(arr.getdata())
    counts: dict[int, int] = {}
    for v in pixels:
        counts[int(v)] = counts.get(int(v), 0) + 1
    return pil.size[0], pil.size[1], counts


# ============== CRUD ==============

@router.post("/masks/upload/{image_id}")
async def upload_mask(
    image_id: int,
    file: UploadFile = File(..., description="mask PNG (P/L mode)"),
    source: str = Query("human", description="ai / human / human_corrected"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    上传 / 替换单图 mask (v3.0.0 Phase 4: thin wrapper, 业务下沉到 SegmentationService.save_uploaded_mask)
    - 同一 image_id 多次上传, 后者覆盖前者
    - 自动读 PNG, 校验 mode, 写 storage_service, mask_path 存到 ORM
    - 写完统计 category_pixel_counts (内存返回, 不入库)
    """
    img = await _ensure_segmentation_image(image_id, db)

    # source 校验
    if source not in {s.value for s in AnnotationSource}:
        raise HTTPException(400, f"source 非法: {source!r}")

    content = await file.read()
    return await SegmentationService.save_uploaded_mask(
        db, img, content, source, user_id=current_user.id,
    )


@router.get("/masks/{image_id}")
async def get_mask(
    image_id: int,
    download: bool = Query(False, description="True=直接返回 PNG 二进制"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    拉取单图 mask
    - download=False: 返回 JSON 元数据 (含 base64 摘要, 不含完整二进制)
    - download=True:  返回 PNG 二进制 (Content-Type: image/png)
    """
    img = await _ensure_segmentation_image(image_id, db)
    m = (await db.execute(
        select(SegmentationMask).where(SegmentationMask.image_id == image_id)
    )).scalar_one_or_none()
    if not m:
        # v2.5.0-s12.7: mask 未标注是常态 (新图), 不抛 404
        # 改为 200 + {file_exists: false}, 前端 loadSegmentationMask 已能识别
        # 避免 F12 Network 大量红色 404 噪音
        return {
            "id": None,
            "image_id": image_id,
            "mask_path": None,
            "width": None,
            "height": None,
            "source": None,
            "annotated_by": None,
            "file_exists": False,
            "file_size": None,
            "created_at": None,
            "updated_at": None,
        }

    if download:
        # 返回 PNG 流
        abs_path = Path(storage_service.base_dir) / m.mask_path
        if not abs_path.is_file():
            raise HTTPException(500, f"mask 文件不存在: {m.mask_path}")
        return Response(
            content=abs_path.read_bytes(),
            media_type="image/png",
            headers={
                "Content-Disposition": (
                    f'inline; filename="mask_{image_id}.png"'
                ),
            },
        )

    # JSON 元数据
    abs_path = Path(storage_service.base_dir) / m.mask_path
    file_exists = abs_path.is_file()
    file_size = abs_path.stat().st_size if file_exists else None
    return {
        "id": m.id,
        "image_id": m.image_id,
        "mask_path": m.mask_path,
        "width": m.width,
        "height": m.height,
        "source": m.source,
        "annotated_by": m.annotated_by,
        "file_exists": file_exists,
        "file_size": file_size,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


@router.delete("/masks/{mask_id}")
async def delete_mask(
    mask_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    删除 mask (v3.0.0 Phase 4: thin wrapper, 业务下沉到 SegmentationService.delete_mask)
    - ORM 软记录直接删, 磁盘文件同时清理
    - 找不到时由 Service 抛 NotFoundError, 全局 handler 统一返回 404
    """
    await SegmentationService.delete_mask(db, mask_id)
    return {
        "success": True,
        "deleted_id": mask_id,
        "file_deleted": True,
    }


@router.post("/masks/replace")
async def replace_mask(
    image_id: int = Query(..., description="图片 id"),
    file: UploadFile = File(..., description="新 mask PNG"),
    source: str = Query("human"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单图 mask 替换: 走 upload 内部逻辑 (覆盖式), 暴露语义化入口
    实际是 upload_mask 的别名, 方便前端批量替换流程
    """
    return await upload_mask(
        image_id=image_id, file=file, source=source,
        db=db, current_user=current_user,
    )
