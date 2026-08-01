"""Update DB model_version.file_path to reflect v3.3.0 subdir structure.

Files have already been moved to {MODEL_DIR}/classification/.
This script only updates DB file_path columns for rows that point to the
old flat location.

Idempotent: only updates rows where file_path does NOT already contain
'classification' / 'detection' / 'segmentation' subdirs.
"""
import os
import sys
from pathlib import Path

backend = Path(__file__).resolve().parent
sys.path.insert(0, str(backend))
os.chdir(backend)

import pymysql
from app.core.config import settings


def main():
    conn = pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE,
    )
    model_dir = settings.MODEL_DIR
    updated = 0
    skipped = 0
    missing = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, task_type, file_path FROM model_version")
            for row_id, name, task_type, file_path in cur.fetchall():
                if not file_path:
                    skipped += 1
                    continue
                # Skip if already in a subdir
                p = Path(file_path)
                if p.parent.name in ("classification", "detection", "segmentation"):
                    print(f"  SKIP (already in subdir): id={row_id} -> {file_path}")
                    skipped += 1
                    continue
                # New path: based on task_type subdir
                subdir_map = {
                    "classification": settings.CLASSIFICATION_MODEL_DIR,
                    "detection": settings.DETECTION_MODEL_DIR,
                    "segmentation": settings.SEGMENTATION_MODEL_DIR,
                }
                if task_type not in subdir_map:
                    print(f"  SKIP (unknown task_type): id={row_id} task={task_type}")
                    skipped += 1
                    continue
                new_path = str((subdir_map[task_type] / p.name).resolve())
                # Verify file exists at new path
                if not Path(new_path).exists():
                    print(f"  WARN (file missing): id={row_id} expected {new_path}")
                    missing += 1
                    # Still update DB so future code knows about the new layout
                cur.execute(
                    "UPDATE model_version SET file_path=%s WHERE id=%s",
                    (new_path, row_id),
                )
                print(f"  UPDATE id={row_id}: {file_path} -> {new_path}")
                updated += 1
        conn.commit()
        print()
        print(f"=== Summary: updated={updated} skipped={skipped} missing={missing} ===")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
