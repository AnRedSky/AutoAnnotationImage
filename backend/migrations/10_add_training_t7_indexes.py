"""
迁移脚本 10: 训练任务表索引优化 (v3.5.0 Phase T7)
================================================
**MIGRATION_ID**: 10
**功能**:
- 优化 L1 阶段审计日志/活动 Feed/统计端点对 training_jobs 表的潜在慢查询
**索引场景**:
- GET /api/audit-logs (filter resource_type=training_job)
- GET /api/training/jobs (训练任务列表) + GET /api/stats/team/{id}
- GET /api/teams/{id}/activities (团队活动 Feed)
**复合索引**:
- ix_training_jobs_base_model   (base_model)             - 单列
- ix_training_jobs_model_name   (model_name)             - 单列
- ix_training_jobs_user_type    (user_id, task_type)     - 复合
- ix_training_jobs_dataset      (dataset_id)             - 单列
**幂等**: 重复执行安全 (information_schema 检查)
**执行顺序**: 10, 依赖 02/03/04/05 (training_jobs 表及字段齐全)
"""
import asyncio
import logging
from sqlalchemy import text
from app.database import engine

logger = logging.getLogger(__name__)


MIGRATION_ID = "10"
MIGRATION_DESCRIPTION = "training_jobs T7 索引优化 (base_model/model_name/user_type/dataset)"


NEW_INDEXES = [
    # v3.5.0 Phase T7: 新增索引
    # 单列索引: 用于按 base_model / model_name 过滤
    ("ix_training_jobs_base_model", "training_jobs", ["base_model"]),
    ("ix_training_jobs_model_name", "training_jobs", ["model_name"]),
    # 复合索引: 用于按用户 + 任务类型过滤 (团队活动 Feed 常用)
    ("ix_training_jobs_user_type", "training_jobs", ["user_id", "task_type"]),
    # 单列索引: 用于按 dataset 查训练历史
    ("ix_training_jobs_dataset", "training_jobs", ["dataset_id"]),
]


async def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """跨 DB 兼容: 检查索引是否已存在"""
    try:
        # MySQL
        result = await conn.execute(text(
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i LIMIT 1"
        ), {"t": table_name, "i": index_name})
        return result.first() is not None
    except Exception:
        try:
            # SQLite
            result = await conn.execute(text(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND tbl_name=:t AND name=:i LIMIT 1"
            ), {"t": table_name, "i": index_name})
            return result.first() is not None
        except Exception:
            return False


async def _create_index_safely(conn, index_name: str, table_name: str, columns: list):
    """创建索引, 失败静默吞 (兼容老 MySQL 5.6)"""
    cols_sql = ", ".join(columns)
    # MySQL 8.0+ 支持 DESC 排序索引; 老版本忽略
    sql = f"CREATE INDEX {index_name} ON {table_name} ({cols_sql})"
    try:
        await conn.execute(text(sql))
        logger.info("[migration] created index %s on %s(%s)", index_name, table_name, cols_sql)
        return True
    except Exception as e:
        # 索引已存在 (race condition) 或老 MySQL 不支持, 静默吞
        logger.warning("[migration] create index %s skipped: %s", index_name, e)
        return False


async def run_migration() -> None:
    """执行迁移: 为 training_jobs 表添加优化索引"""
    async with engine.begin() as conn:
        for index_name, table_name, columns in NEW_INDEXES:
            if await _index_exists(conn, table_name, index_name):
                logger.info("[migration] index %s already exists, skip", index_name)
                continue
            await _create_index_safely(conn, index_name, table_name, columns)

    logger.info(f"[done] [{MIGRATION_ID}] {MIGRATION_DESCRIPTION}")


async def main():
    """兼容历史 CLI 调用"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await run_migration()


if __name__ == "__main__":
    asyncio.run(main())
