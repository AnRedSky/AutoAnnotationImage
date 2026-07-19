"""v2.0.0 任务类型拓展 - 数据库迁移

新增:
1. bbox_annotation 表 (BBox 标注)
2. segmentation_mask 表 (Mask 标注)
3. image.task_type 字段 (默认 classification, 兼容 v1.0.0 历史数据)
4. model_version.task_type 字段 (默认 classification)
5. model_version 任务专属指标: map_50 / map_50_95 / miou / pixel_accuracy / dice_score

幂等: 重复执行安全 (用 information_schema.COLUMNS + SHOW TABLES 判定)
"""
import asyncio
from sqlalchemy import text
from app.database import engine, Base
from app.models import (  # noqa: F401  触发 SQLAlchemy metadata 注册
    User, Dataset, Category, Image, AnnotationLog, ModelVersion, TrainingJob,
    BBoxAnnotation, SegmentationMask,
)


# ================== 1. 新建表 (SQLAlchemy create_all 已自动建) ==================
# 跑 create_all 会自动建 bbox_annotation / segmentation_mask (新表)
# 这里只负责建 index 和外键约束的额外检查


# ================== 2. 已有表加列 (MySQL 5.7+ / SQLite) ==================

ADD_COLUMN_STATEMENTS = [
    # image.task_type
    ("image", "task_type",
     "ALTER TABLE image ADD COLUMN task_type VARCHAR(20) NOT NULL DEFAULT 'classification'"),
    # 单独建索引 (MySQL 不支持 IF NOT EXISTS 在 ADD INDEX 上, 用 procedure 包一下)
    ("image", "idx_image_task_type",
     "CREATE INDEX idx_image_task_type ON image (task_type)"),

    # model_version.task_type
    ("model_version", "task_type",
     "ALTER TABLE model_version ADD COLUMN task_type VARCHAR(20) NOT NULL DEFAULT 'classification'"),
    ("model_version", "idx_model_task_type",
     "CREATE INDEX idx_model_task_type ON model_version (task_type)"),

    # model_version 检测指标
    ("model_version", "map_50",
     "ALTER TABLE model_version ADD COLUMN map_50 FLOAT NULL"),
    ("model_version", "map_50_95",
     "ALTER TABLE model_version ADD COLUMN map_50_95 FLOAT NULL"),
    # model_version 分割指标
    ("model_version", "miou",
     "ALTER TABLE model_version ADD COLUMN miou FLOAT NULL"),
    ("model_version", "pixel_accuracy",
     "ALTER TABLE model_version ADD COLUMN pixel_accuracy FLOAT NULL"),
    ("model_version", "dice_score",
     "ALTER TABLE model_version ADD COLUMN dice_score FLOAT NULL"),

    # v2.0.0 S3.2: training_jobs.task_type (区分 classification / detection / segmentation)
    ("training_jobs", "task_type",
     "ALTER TABLE training_jobs ADD COLUMN task_type VARCHAR(32) NOT NULL DEFAULT 'classification'"),
    ("training_jobs", "idx_training_jobs_task_type",
     "CREATE INDEX idx_training_jobs_task_type ON training_jobs (task_type)"),
]


async def _column_exists(conn, table: str, column: str) -> bool:
    """检查列是否存在 (兼容 MySQL / SQLite)"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        cols = [row[1] for row in result.fetchall()]
        return column in cols
    # MySQL / 其他
    result = await conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table
          AND COLUMN_NAME = :column
    """), {"table": table, "column": column})
    return (result.scalar() or 0) > 0


async def _index_exists(conn, table: str, index_name: str) -> bool:
    """检查索引是否存在 (仅 MySQL, SQLite 跳过)"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        # SQLite index 不可重复创建, 重复执行时由 SQLAlchemy 抛错但被 try/except 吞掉
        return False
    result = await conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table
          AND INDEX_NAME = :index_name
    """), {"table": table, "index_name": index_name})
    return (result.scalar() or 0) > 0


async def run_migration():
    async with engine.begin() as conn:
        # 1. 先建所有新表 (create_all 只建不存在的)
        await conn.run_sync(Base.metadata.create_all)
        print("[OK] create_all: 新表 (bbox_annotation / segmentation_mask) 已就位")

        # 2. 已有表加列
        for table, name, sql in ADD_COLUMN_STATEMENTS:
            is_column = not name.startswith("idx_") and "INDEX" not in sql.upper().split("ADD COLUMN")[0]
            is_index = name.startswith("idx_") or "INDEX" in sql.upper()

            try:
                if is_column:
                    if not await _column_exists(conn, table, name):
                        await conn.execute(text(sql))
                        print(f"[OK] ALTER TABLE {table} ADD COLUMN {name}")
                    else:
                        print(f"[SKIP] {table}.{name} 已存在")
                elif is_index:
                    if not await _index_exists(conn, table, name):
                        await conn.execute(text(sql))
                        print(f"[OK] CREATE INDEX {name} ON {table}")
                    else:
                        print(f"[SKIP] index {name} 已存在")
            except Exception as e:
                # SQLite 上 CREATE INDEX 重复会报错, 静默跳过
                msg = str(e).lower()
                if "already exists" in msg or "duplicate" in msg:
                    print(f"[SKIP] {name} (重复创建)")
                else:
                    raise


async def main():
    await run_migration()
    print("\n=== v2.0.0 迁移完成 ===")
    print("- 新表: bbox_annotation, segmentation_mask")
    print("- image.task_type (默认 classification)")
    print("- model_version.task_type + 5 个新指标字段")
    print("- training_jobs.task_type (S3.2: 区分 classification / detection / segmentation)")


if __name__ == "__main__":
    asyncio.run(main())
