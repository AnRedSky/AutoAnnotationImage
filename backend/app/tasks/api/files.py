"""
File Serving API
================
为前端 Annotate.vue / DatasetDetail.vue 提供图片二进制流代理接口：
  GET /api/files/{image_id}
  GET /api/files/{image_id}/thumbnail?size=240
  GET /api/files/{image_id}/metadata
前端 <img :src="imageApi.fileUrl(img.id, token)"> 直接可用。

鉴权策略 (v3.0.0 全面审查修复 P0-2):
  - 必须鉴权: 匿名用户不再允许访问任何文件
  - Authorization Bearer 头优先, ?token=xxx 兜底 (EventSource/<img> 无法设 header)
  - 鉴权成功后校验: current_user.can_access_dataset(image.dataset)
  - 管理员可访问全部, 普通用户仅可访问自己拥有的数据集
  - 跨用户访问 → 403
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging

from app.database import get_db
from app.tasks.model.image import Image
from app.tasks.model.dataset import Dataset
from app.admin.model.user import User
from app.middleware.http.auth import get_user_optional_for_query

logger = logging.getLogger(__name__)
router = APIRouter()


# 常见图片 MIME 类型映射
_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}


def _require_user(
    user: User | None,
    image: Image,
) -> None:
    """v3.0.0 全面审查修复 P0-2: 文件端点强制鉴权 + 数据集权限校验

    流程:
      1) 未登录 → 401
      2) 已登录但无权限访问该数据集 → 403
    """
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required to access image files",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 权限检查: 管理员可访问全部, 否则只允许 owner
    if not user.can_access_dataset(image.dataset):
        logger.warning(
            "Permission denied: user_id=%s attempted to access image_id=%s "
            "(dataset_id=%s, owner_id=%s)",
            user.id, image.id, image.dataset_id, image.dataset.owner_id,
        )
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this image",
        )


@router.get("/{image_id}")
async def get_image_file(
    image_id: int,
    request: Request,
    token: str | None = Query(default=None, description="JWT token (for <img> tag)"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_user_optional_for_query),
):
    """
    通过图片 ID 返回图片二进制流
    - 必须鉴权 (Bearer header 或 ?token=xxx)
    - 鉴权后校验数据集权限
    - 自动按文件后缀设置 Content-Type
    """
    # 1) 取图片 + 关联 dataset
    result = await db.execute(
        select(Image, Dataset)
        .join(Dataset, Image.dataset_id == Dataset.id)
        .where(Image.id == image_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    img, dataset = row

    # 2) 强制鉴权 + 权限校验
    _require_user(current_user, img)

    # 3) 取文件内容
    from app.common.storage.storage_service import storage_service
    if not storage_service.exists(img.storage_path):
        raise HTTPException(status_code=404, detail="Image file missing on storage")

    content = await storage_service.load(img.storage_path)

    # 根据 storage_path 后缀确定 MIME
    from pathlib import Path
    ext = Path(img.storage_path).suffix.lower()
    media_type = _MIME_MAP.get(ext, "application/octet-stream")

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Length": str(len(content)),
        },
    )


@router.head("/{image_id}")
async def head_image_file(
    image_id: int,
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_user_optional_for_query),
):
    """HEAD 方法：仅返回元数据，供前端做预检/缓存策略

    v3.0.0 P0-2: 同样需要鉴权, 否则暴露 image_id 是否存在
    """
    result = await db.execute(
        select(Image, Dataset)
        .join(Dataset, Image.dataset_id == Dataset.id)
        .where(Image.id == image_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    img, _ = row

    _require_user(current_user, img)

    from app.common.storage.storage_service import storage_service
    if not storage_service.exists(img.storage_path):
        raise HTTPException(status_code=404, detail="Image file missing on storage")

    return Response(
        content=None,
        media_type="image/jpeg",
        headers={"Content-Length": str(img.file_size or 0)},
    )


@router.get("/{image_id}/thumbnail")
async def get_image_thumbnail(
    image_id: int,
    size: int = Query(default=240, ge=32, le=1024, description="最大边长像素"),
    request: Request = None,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_user_optional_for_query),
):
    """
    返回图片缩略图 (JPEG 格式, 最长边 = size)
    - 必须鉴权 + 校验数据集权限
    - 优先用进程内 LRU 缓存 (Phase V: 同一 image_id+size 第二次起 1ms 内返)
    - 缓存 miss 时用 PIL 缩放, 不缓存磁盘
    - 缺失图片返回 404
    """
    result = await db.execute(
        select(Image, Dataset)
        .join(Dataset, Image.dataset_id == Dataset.id)
        .where(Image.id == image_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    img, _ = row

    _require_user(current_user, img)

    from app.common.storage.storage_service import storage_service
    from pathlib import Path as _P
    if not storage_service.exists(img.storage_path):
        raise HTTPException(status_code=404, detail="Image file missing on storage")

    # 缩略图 cache 加载器: PIL 编码一次. LRU 在 app/common/cache/thumbnail_cache.py.
    from app.common.cache.thumbnail_cache import thumbnail_for

    def loader(image_id_: int, size_: int) -> bytes:
        """cache miss 时调用. 必须读 storage + PIL 编一次."""
        from PIL import Image as PILImage
        from io import BytesIO
        from app.utils.async_helpers import run_async_in_worker

        # cache 调到 loader 在 worker thread 内, 但 storage_service.load 是 async.
        # 用 run_async_in_worker 同步驱动 async 协程.
        content = run_async_in_worker(storage_service.load(img.storage_path))
        if not content:
            raise RuntimeError(f"image {image_id_} empty")
        pil_img = PILImage.open(BytesIO(content))
        pil_img.thumbnail((size_, size_), PILImage.LANCZOS)
        if pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")
        buf = BytesIO()
        pil_img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()

    # 先读 storage 一遍拿 bytes (loader 内部也读; 但 cache miss 时不重复 PIL)
    # 简化: cache key = (image_id, size), value = encoded JPEG bytes.
    # 第一次: loader 调 -> PIL encode -> store
    # 第二次: cache hit -> 直接返, loader 完全不调
    try:
        thumb_bytes = thumbnail_for(image_id, size, loader=loader)
    except Exception as e:
        # 缩略图生成失败时回退到原图
        logger.warning("thumbnail generation failed for image %s: %s", image_id, e)
        content = await storage_service.load(img.storage_path)
        ext = _P(img.storage_path).suffix.lower()
        return Response(
            content=content,
            media_type=_MIME_MAP.get(ext, "application/octet-stream"),
            headers={"Cache-Control": "private, max-age=3600"},
        )

    return Response(
        content=thumb_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Length": str(len(thumb_bytes)),
        },
    )
