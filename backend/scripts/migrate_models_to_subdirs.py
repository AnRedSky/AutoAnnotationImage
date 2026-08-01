"""Migrate existing flat models to v3.3.0 task-type subdirs.

Before:  backend/models/resnet50_v1_*_best.pth        (flat)
After:   backend/models/classification/resnet50_v1_*_best.pth

Steps:
1. Read all model files in MODEL_DIR root (*.pth / *.pt)
2. For each: detect task_type from DB ModelVersion.base_model + name pattern
3. Move to the appropriate subdir
4. If file is referenced in ModelVersion.file_path, update the DB row

DB update is done via raw SQL UPDATE (avoid model import overhead).
"""
import os
import shutil
import sys
from pathlib import Path

# Setup
backend = Path(__file__).resolve().parent
sys.path.insert(0, str(backend))
os.chdir(backend)

import asyncio
from sqlalchemy import select, text
from app.database import AsyncSessionLocal
from app.core.config import settings


async def get_mv_paths() -> dict[str, str]:
    """Return {file_path: task_type} for all ModelVersion rows."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("SELECT file_path, task_type FROM model_version")
        )
        return {row[0]: row[1] for row in result if row[0]}


def detect_task_type(filename: str) -> str:
    """Heuristic for files NOT in DB (orphan)."""
    n = filename.lower()
    if "yolo" in n or "detect" in n:
        return "detection"
    if "seg" in n or "deeplab" in n or "fcn" in n:
        return "segmentation"
    # default: classification
    return "classification"


async def main():
    mv_paths = await get_mv_paths()
    print(f"DB has {len(mv_paths)} ModelVersion file_path entries")

    moved = 0
    skipped = 0
    model_dir = settings.MODEL_DIR
    subdir_map = {
        "classification": settings.CLASSIFICATION_MODEL_DIR,
        "detection": settings.DETECTION_MODEL_DIR,
        "segmentation": settings.SEGMENTATION_MODEL_DIR,
    }

    for src in sorted(model_dir.glob("*.pth")) + sorted(model_dir.glob("*.pt")):
        if not src.is_file():
            continue
        # Skip if already in a subdir (parent != model_dir)
        if src.parent.resolve() != model_dir.resolve():
            continue
        # Find task_type: try DB first, fallback to filename heuristic
        # mv_paths is keyed by file_path; try both absolute and resolved forms
        abs_path = str(src.resolve())
        if abs_path in mv_paths:
            task_type = mv_paths[abs_path]
            source = "DB"
        elif str(src) in mv_paths:
            task_type = mv_paths[str(src)]
            source = "DB"
        else:
            task_type = detect_task_type(src.name)
            source = "heuristic"
        # Detection goes one level deeper: detection/{name}/weights/best.pt
        # Since these are classification .pth (flat, not YOLO runs), keep flat in subdir
        subdir = subdir_map[task_type]
        dst = subdir / src.name
        if dst.exists():
            print(f"  SKIP (exists): {src.name} -> {dst} [{source}: {task_type}]")
            skipped += 1
            continue
        print(f"  MOVE: {src.name} -> {dst.relative_to(model_dir)} [{source}: {task_type}]")
        try:
            shutil.move(str(src), str(dst))
            moved += 1
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        # Update DB if this file was referenced
        if abs_path in mv_paths or str(src) in mv_paths:
            old_key = abs_path if abs_path in mv_paths else str(src)
            new_path = str(dst.resolve())
            try:
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        text("UPDATE model_version SET file_path = :new WHERE file_path = :old"),
                        {"new": new_path, "old": old_key},
                    )
                    await db.commit()
                    print(f"    DB updated: {old_key} -> {new_path}")
            except Exception as e:
                print(f"    DB update FAILED: {e}")

    print()
    print(f"=== Summary: moved={moved} skipped={skipped} ===")


if __name__ == "__main__":
    asyncio.run(main())
