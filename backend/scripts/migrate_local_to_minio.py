"""
迁移脚本: local filesystem → MinIO (一次性)
==========================================

用法:
    cd backend
    env -u PYTHONPATH ./.venv/Scripts/python.exe scripts/migrate_local_to_minio.py

前提:
    1. 已设置 .env: STORAGE_BACKEND=minio (或脚本里强制覆盖)
    2. MINIO_ENDPOINT / ACCESS_KEY / SECRET_KEY / BUCKET 已配置
    3. local 文件仍在 settings.UPLOAD_DIR 下

行为:
    - 读 image 表所有 storage_path
    - 读本地文件 → put_object 到 MinIO
    - 验证 size + sha256 一致后才删本地文件 (幂等, 中途失败可重跑)
    - 默认 dry-run, --apply 才会真的上传+删
"""
import argparse
import asyncio
import hashlib
import logging
import os
import sys
from pathlib import Path

# 把 backend 加到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.common.storage.minio_storage_service import MinioStorageService
from app.common.storage.storage_service import StorageService
from app.core.config import settings
from app.database.session import AsyncSessionLocal
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("migrate_local_to_minio")


async def migrate(dry_run: bool = True, batch_size: int = 50):
    if not (settings.STORAGE_BACKEND or "local").lower() == "minio":
        log.error("STORAGE_BACKEND 必须为 minio (当前: %s)", settings.STORAGE_BACKEND)
        sys.exit(1)

    local = StorageService()  # 用本地 base_dir
    remote = MinioStorageService()
    upload_dir = Path(settings.UPLOAD_DIR).resolve()

    log.info("local base:  %s", upload_dir)
    log.info("remote bucket: %s @ %s", settings.MINIO_BUCKET, settings.MINIO_ENDPOINT)
    log.info("mode: %s", "DRY-RUN" if dry_run else "APPLY (真迁移)")

    # 强制建 bucket (幂等)
    try:
        remote._ensure_bucket()
    except Exception as e:
        log.error("bucket check failed: %r", e)
        sys.exit(1)

    async with AsyncSessionLocal() as db:
        result = await db.execute(text("SELECT id, storage_path, file_hash FROM image"))
        rows = result.all()
        log.info("found %d images in DB", len(rows))

        migrated = 0
        skipped = 0
        errors = 0
        for i, row in enumerate(rows):
            image_id, storage_path, file_hash = row[0], row[1], row[2]
            if not storage_path:
                skipped += 1
                continue
            local_file = upload_dir / storage_path
            if not local_file.exists():
                log.warning("[id=%s] local file missing: %s — skip", image_id, local_file)
                skipped += 1
                continue
            try:
                content = local_file.read_bytes()
                # 校验 hash
                actual_hash = hashlib.sha256(content).hexdigest()
                if file_hash and actual_hash != file_hash:
                    log.error("[id=%s] hash mismatch! db=%s file=%s", image_id, file_hash, actual_hash)
                    errors += 1
                    continue
                # 验证 MinIO 上没这个 key (避免重复)
                if remote.exists(storage_path):
                    log.info("[id=%s] already in MinIO, skip", image_id)
                    skipped += 1
                    continue
                if dry_run:
                    log.info("[id=%s] would upload %s (%d bytes)", image_id, storage_path, len(content))
                else:
                    await remote.save(storage_path, content)
                    # 验证
                    fetched = await remote.load(storage_path)
                    assert len(fetched) == len(content), f"size mismatch: {len(fetched)} vs {len(content)}"
                    log.info("[id=%s] uploaded %s (%d bytes) ✓", image_id, storage_path, len(content))
                    local_file.unlink()
                    migrated += 1
            except Exception as e:
                log.error("[id=%s] failed: %r", image_id, e)
                errors += 1

            if (i + 1) % batch_size == 0:
                log.info("progress: %d/%d", i + 1, len(rows))

        log.info("\n=== SUMMARY ===")
        log.info("total:    %d", len(rows))
        log.info("migrated: %d", migrated if not dry_run else "n/a (dry-run)")
        log.info("skipped:  %d", skipped)
        log.info("errors:   %d", errors)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="真迁移 (默认 dry-run)")
    args = p.parse_args()
    asyncio.run(migrate(dry_run=not args.apply))