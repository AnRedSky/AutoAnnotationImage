"""
image.upload 模块 — 图片上传相关 API
=====================================

v3.0.0 Phase N 拆分: 从 image.py 抽离
- 职责: 批量上传图片到指定数据集
- 安全:
  - 扩展名白名单 (.jpg/.jpeg/.png/.bmp/.webp/.gif)
  - 文件大小硬限 (MAX_UPLOAD_SIZE_BYTES, 默认 20MB)
  - Magic bytes 校验 (用 PILImage.verify, 防止扩展名伪装)
  - filename sanitize (safe_filename 去除路径分隔符)
  - aiofiles 异步 IO (P0 修复)
"""
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image as PILImage
from io import BytesIO
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.common.storage.storage_service import storage_service
from app.core.config import settings
from app.utils.file_utils import safe_filename

# 上传相关独立 router (prefix 需在 image/__init__.py 装配时统一加)
router = APIRouter()

# v3.0.0 审查修复 P0-4: 文件上传白名单
_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}


def _validate_image_file(file: UploadFile, content: bytes) -> tuple[bool, str]:
    """P0-4 修复: 综合校验扩展名/大小/魔术字节

    Returns:
        (ok, err_msg)
    """
    if len(content) == 0:
        return False, "文件为空"
    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        mb = len(content) // (1024 * 1024)
        return False, f"文件过大 ({mb}MB > {settings.MAX_UPLOAD_SIZE_MB}MB)"

    # 1) 扩展名白名单
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        return False, f"不支持的文件类型: {ext!r}, 允许: {sorted(_ALLOWED_IMAGE_EXTS)}"

    # 2) Magic bytes 校验 (PIL parse 失败说明不是真实图片)
    try:
        img = PILImage.open(BytesIO(content))
        img.verify()  # 仅校验格式不解码
    except Exception as e:
        return False, f"文件不是有效的图片 ({e!s})"

    return True, ""


@router.post("/upload/{dataset_id}")
async def upload_images(
    dataset_id: int,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量上传图片到指定数据集
    Returns: 上传结果列表 (含 ID、文件名、是否去重)

    v3.0.0 审查修复 P0-4:
    - 扩展名白名单 (拒绝 .py/.html/.zip 等)
    - 大小硬限 (单文件 > 20MB 直接 413)
    - Magic bytes 校验 (拒绝扩展名伪装)
    - filename sanitize (去除路径分隔符)
    """
    # 校验数据集
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # P0-3: 非 admin 只能向自己的 dataset 上传
    if not current_user.is_admin() and dataset.owner_id != current_user.id:
        raise HTTPException(403, "无权限向此数据集上传图片")

    results = []
    for file in files:
        # P0-4 修复 1: 流式读取 + 大小限制 (避免内存爆炸)
        # 限制为 MAX_UPLOAD_SIZE_BYTES + 1KB 缓冲
        content = await file.read(settings.MAX_UPLOAD_SIZE_BYTES + 1024)
        if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                413,
                f"文件 {file.filename!r} 超过 {settings.MAX_UPLOAD_SIZE_MB}MB 限制"
            )

        # P0-4 修复 2: 多重校验
        ok, err = _validate_image_file(file, content)
        if not ok:
            raise HTTPException(400, f"文件 {file.filename!r} 校验失败: {err}")

        # P0-4 修复 3: filename sanitize
        safe_name = safe_filename(file.filename or "unnamed")
        file_hash = storage_service.compute_hash(content)

        # 去重
        existing = await db.execute(
            select(Image).where(
                Image.dataset_id == dataset_id,
                Image.file_hash == file_hash,
            )
        )
        if existing.scalar_one_or_none():
            results.append({"filename": safe_name, "duplicate": True})
            continue

        # 保存文件
        storage_key = storage_service.generate_key(dataset_id, safe_name, file_hash)
        await storage_service.save(storage_key, content)

        # 读取图片尺寸
        try:
            pil_img = PILImage.open(BytesIO(content))
            width, height = pil_img.size
        except Exception:
            width, height = None, None

        # 写库
        # v2.0.0: 冗余 task_type 到 image 表, 避免后续每条都 join dataset
        img = Image(
            dataset_id=dataset_id,
            filename=safe_name,
            storage_path=storage_key,
            file_size=len(content),
            width=width,
            height=height,
            file_hash=file_hash,
            status="pending",
            task_type=dataset.task_type,  # 同步数据集的 task_type
        )
        db.add(img)
        results.append({"filename": safe_name, "duplicate": False})

    # 更新数据集图片数
    await db.execute(
        update(Dataset)
        .where(Dataset.id == dataset_id)
        .values(image_count=Dataset.image_count + len([r for r in results if not r["duplicate"]]))
    )
    await db.commit()

    return {
        "total": len(files),
        "uploaded": len([r for r in results if not r["duplicate"]]),
        "duplicates": len([r for r in results if r["duplicate"]]),
        "items": results,
    }
