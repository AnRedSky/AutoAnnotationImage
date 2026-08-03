"""
app.common.storage.inference_resolver — 推理路径解析器
=====================================================

**v3.4.1 P1 修复**: 让 batch_predict / predict_image_grouped / seg_predict
适配两种 STORAGE_BACKEND (local / minio), 解决 MinIO 后端推理报
FileNotFoundError 的问题.

**背景**:
- 推理栈 (PIL.Image.open / cv2.imread / ultralytics.YOLO.predict) 都需要
  真实文件路径, 不支持直接读 bytes.
- 旧实现: `image_paths = [str(storage_root / img.storage_path) for img in images]`
  在 MinIO 后端下, storage_root 是空目录, 路径在磁盘上不存在 → FileNotFoundError.

**方案**:
- 本地后端: 直接返回 base_dir/key 的绝对路径 (零开销).
- MinIO 后端: 在临时目录下载文件, 返回临时文件路径, 退出上下文时自动清理.

**API**:
```python
async with resolve_inference_paths(images) as image_paths:
    predictions = await ai_service.batch_predict(image_paths, ...)
    # image_paths 顺序与 images 顺序一致, MinIO 模式下用完自动清理
```
"""
import asyncio
import logging
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, List, Sequence

import aiofiles

from app.core.config import settings
from app.common.storage import get_storage_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def resolve_inference_paths(
    images: Sequence,  # 任意含 .storage_path 属性的对象 (Image ORM 行)
) -> AsyncIterator[List[str]]:
    """解析 image 列表为本地可读路径 (PIL/cv2/YOLO).

    根据 STORAGE_BACKEND 自动选择:
    - local: 直接拼 base_dir/key, 无 IO 开销
    - minio: 在临时目录下载所有图, 退出时清理

    Args:
        images: 任意序列, 每项必须有 .storage_path 属性 (str).

    Yields:
        list[str]: 与 images 顺序一致的本地文件路径列表.
                   MinIO 模式下是临时文件路径, 退出 with 后被删除.

    Raises:
        FileNotFoundError: MinIO load 抛 (key 不存在).
    """
    storage_keys: List[str] = [img.storage_path for img in images]
    backend = (settings.STORAGE_BACKEND or "local").lower()

    # ---- 本地后端: 零开销直接返回 ----
    if backend == "local":
        base = settings.UPLOAD_DIR
        yield [str(base / k) for k in storage_keys]
        return

    # ---- MinIO 后端: 全部下载到临时目录 ----
    svc = get_storage_service()
    tmpdir = Path(tempfile.mkdtemp(prefix="img_inference_"))
    paths: List[str] = []
    logger.debug(
        "resolve_inference_paths: downloading %d images to %s (backend=minio)",
        len(storage_keys), tmpdir,
    )
    try:
        # 并发下载 (MinIO SDK 同步, 用 asyncio.to_thread 并发拉)
        async def _download(k: str) -> str:
            content = await svc.load(k)
            ext = Path(k).suffix or ".bin"
            tmp_path = tmpdir / f"{uuid.uuid4().hex}{ext}"
            async with aiofiles.open(tmp_path, "wb") as f:
                await f.write(content)
            return str(tmp_path)

        # 用 gather 并发下载, 但保留顺序
        paths = await asyncio.gather(*[_download(k) for k in storage_keys])
        yield list(paths)
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
            logger.debug("resolve_inference_paths: cleaned up %s", tmpdir)
        except Exception as e:  # noqa: BLE001
            logger.warning("resolve_inference_paths: failed to clean %s: %s", tmpdir, e)


__all__ = ["resolve_inference_paths"]
