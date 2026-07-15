"""
File Serving API
================
为前端 Annotate.vue / DatasetDetail.vue 提供图片二进制流代理接口：
  GET /api/files/{image_id}
  GET /api/files/{image_id}/thumbnail?size=240
前端 <img :src="imageApi.fileUrl(img.id)"> 直接可用。

鉴权策略：可选鉴权（论文 demo / 内部系统）
  - 带 Authorization Bearer xxx 或 ?token=xxx → 校验（验证失败仍 401）
  - 都不带 → 允许访问（返回图片）
  - 设计依据：<img> 标签无法附加 header，query token 也不是 100% 可靠
  - 生产环境如需严格权限，可在 storage_service 后面再加 dataset 权限校验
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging

from app.database import get_db
from app.models.image import Image
from app.models.user import User
from app.core.security import decode_token
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_user_optional(
    request: Request,
    token: str | None = Query(default=None, description="JWT token (for <img> tag)"),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """可选鉴权：能拿到 user 就返回，拿不到返回 None
    - Authorization header 优先
    - 其次 query ?token=xxx
    - 都没有 → None（调用方决定是否允许匿名）
    - 有 token 但无效 → 抛 401（不能静默放行无效凭证）
    """
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    try:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
    except Exception:
        user = None
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    return user


# 常见图片 MIME 类型映射
_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}


@router.get("/{image_id}")
async def get_image_file(
    image_id: int,
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_user_optional),
):
    """
    通过图片 ID 返回图片二进制流
    - 鉴权可选：带 token 则校验 user，不带也允许（demo/内部使用）
    - 自动按文件后缀设置 Content-Type
    """
    result = await db.execute(select(Image).where(Image.id == image_id))
    img = result.scalar_one_or_none()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

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
    current_user: User | None = Depends(get_user_optional),
):
    """HEAD 方法：仅返回元数据，供前端做预检/缓存策略"""
    result = await db.execute(select(Image).where(Image.id == image_id))
    img = result.scalar_one_or_none()
    if not img or not storage_service.exists(img.storage_path):
        raise HTTPException(status_code=404, detail="Image not found")
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
    current_user: User | None = Depends(get_user_optional),
):
    """
    返回图片缩略图 (JPEG 格式, 最长边 = size)
    - 鉴权可选
    - 使用 PIL 实时缩放, 不缓存磁盘 (简单实现)
    - 缺失图片返回 404
    """
    result = await db.execute(select(Image).where(Image.id == image_id))
    img = result.scalar_one_or_none()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")
    if not storage_service.exists(img.storage_path):
        raise HTTPException(status_code=404, detail="Image file missing on storage")

    try:
        from PIL import Image as PILImage
        from io import BytesIO
        content = await storage_service.load(img.storage_path)
        pil_img = PILImage.open(BytesIO(content))
        # 等比缩放
        pil_img.thumbnail((size, size), PILImage.LANCZOS)
        # 统一转 RGB
        if pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")
        buf = BytesIO()
        pil_img.save(buf, format="JPEG", quality=80)
        thumb_bytes = buf.getvalue()
        return Response(
            content=thumb_bytes,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "private, max-age=86400",
                "Content-Length": str(len(thumb_bytes)),
            },
        )
    except Exception as e:
        # 缩略图生成失败时回退到原图
        logger.warning("thumbnail generation failed for image %s: %s", image_id, e)
        content = await storage_service.load(img.storage_path)
        from pathlib import Path as _P
        ext = _P(img.storage_path).suffix.lower()
        return Response(
            content=content,
            media_type=_MIME_MAP.get(ext, "application/octet-stream"),
            headers={"Cache-Control": "private, max-age=3600"},
        )
